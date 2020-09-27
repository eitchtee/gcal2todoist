import signal
from time import sleep
import datetime
import os
import logging

import todoist
from gcsa.google_calendar import GoogleCalendar
import yaml
from tinydb import TinyDB, Query
from dateutil.parser import parse


# Initialize logger handle
logging.getLogger('googleapiclient').setLevel(logging.CRITICAL)
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class GracefulKiller:
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self):
        self.kill_now = True


class Configs:
    def __init__(self):
        configs_path = os.path.join(os.path.dirname(__file__), 'configs.yml')
        with open(configs_path, encoding='utf8') as file:
            data = yaml.load(file, Loader=yaml.FullLoader)

        self.log_level = data.get('log_level', 'INFO')
        logger.setLevel(self.log_level)

        self.token = data.get('todoist_api_token')
        self.project = data.get('default_project')
        self.label = data.get('label')
        self.keep_running = data.get('keep_running')
        self.run_every = data.get('run_every')
        self.calendars = data.get('calendars', ['primary'])

        self.todoist_api = todoist.TodoistAPI(self.token)
        self.calendar = []
        self.db = TinyDB(os.path.join(os.path.dirname(__file__), 'events.json'))

        self.project_id = None
        self.label_id = None

    def refresh_calendar(self):
        for calendar in self.calendars:
            logger.info(f'Getting calendar: "{calendar}"')
            self.calendar += list(GoogleCalendar(calendar,
                                                 credentials_path=os.path.join(
                                                     os.path.dirname(__file__),
                                                     '.credentials',
                                                     'credentials.json')))


def fetch_project_id():
    logger.info('Fetching desired project-id')
    matching_projects = [project for project
                         in cf.todoist_api.state['projects'] if
                         project['name'] == Configs().project]
    if len(matching_projects) >= 1:
        proj_id = matching_projects[0]['id']
    else:
        new_project = cf.todoist_api.projects.add(Configs().project)
        cf.todoist_api.commit()
        proj_id = new_project['id']

    logger.info(f'Project-id found: {proj_id}')
    return proj_id


def fetch_label_id():
    logger.info('Fetching desired label-id')
    matching_labels = [label for label in cf.todoist_api.state['labels'] if
                       label['name'] == Configs().label]
    if len(matching_labels) >= 1:
        r_label_id = matching_labels[0]['id']
    else:
        new_label = cf.todoist_api.labels.add(Configs().label)
        cf.todoist_api.commit()
        r_label_id = new_label['id']

    logger.info(f'Label-id found: {r_label_id}')
    return r_label_id


def generate_note(location, description):
    note = []

    if location:
        note.append(f"📍 {location}")
    if description:
        if len(note) >= 1:
            note.append('\n\n')
        note.append(f"📝 {description}")
    if len(note) == 0:
        note.append('❌')

    note = ''.join(note)

    return note


def generate_task_name(title):
    return f"* 🗓️ **{title}**"


def generate_desired_dates(event):
    date_range = [str(event.start + datetime.timedelta(days=x)) for x in
                  range(0, (event.end - event.start).days)]

    if not date_range:
        date_range.append(str(event.start))

    return date_range


def add_task(event):
    api = cf.todoist_api

    task = generate_task_name(event.summary)
    note = generate_note(event.location, event.description)
    dates = generate_desired_dates(event)

    logger.info(f'Handling task: {event.summary}')

    for date in dates:
        search = Query()
        if not cf.db.search((search.event_id == event.id) &
                            (search.due_string == date)):
            item = api.add_item(content=task,
                                project_id=cf.project_id,
                                labels=[cf.label_id],
                                date_string=str(date),
                                note=note)

            api.commit()

            cf.db.insert({'event_id': event.id,
                          'task_id': item['id'],
                          'note_id': item['note']['id'],
                          'due_string': date})
        else:
            search = Query()
            result = cf.db.search((search.event_id == event.id) &
                                  (search.due_string == date))[0]
            task_id = result['task_id']

            if not api.items.get(task_id):
                item = api.add_item(content=task,
                                    project_id=cf.project_id,
                                    labels=[cf.label_id],
                                    date_string=str(date),
                                    note=note)

                api.commit()

                cf.db.update({'event_id': event.id,
                              'task_id': item['id'],
                              'note_id': item['note']['id'],
                              'due_string': date},
                             (search.event_id == event.id) &
                             (search.due_string == date))


def clear_yesterday_tasks(event):
    api = cf.todoist_api

    tasks = cf.db.search(Query().event_id == event.id)

    for i, task in enumerate(tasks):
        due_date = parse(task['due_string'])
        if due_date.date() < datetime.datetime.today().date():
            logger.info(f'Removing stale "{i}" task from "{event.summary}"')
            api.items.complete(task['task_id'])
            api.commit()
            cf.db.remove(Query().task_id == task['task_id'])


def clear_unattached_task(event, task_id, due_date):
    api = cf.todoist_api

    dates = generate_desired_dates(event)

    if due_date not in dates:
        logger.info(f'Removing unattached task from {event.summary}')
        api.items.delete(task_id)
        api.commit()
        cf.db.remove((Query().task_id == task_id) &
                     (Query().due_string == due_date))
        return True

    return False


def update_task_name(event, task_id):
    title = generate_task_name(event.summary)

    item = cf.todoist_api.items.get(task_id)
    if item:
        if item['item']['content'] != title:
            logger.info(f'Updating task name for: "{event.summary}"')
            cf.todoist_api.items.update(task_id, content=title)


def update_task_note(event, note_id):
    api = cf.todoist_api
    note_content = generate_note(event.location, event.description)

    note = api.notes.get(note_id)
    if note:
        if note.get('note')['content'] != note_content:
            logger.info(f'Updating note for: "{event.summary}"')
            cf.todoist_api.notes.update(note_id, content=note_content)


def main():
    db = cf.db
    api = cf.todoist_api
    calendar = cf.calendar

    for event in calendar:
        add_task(event)
        clear_yesterday_tasks(event)

    all_event_ids = [x.id for x in calendar]
    for entry in db:
        task_id = entry.get('task_id')
        note_id = entry.get('note_id')
        event_id = entry.get('event_id')
        event = [x for x in cf.calendar if
                 x.id == event_id][0]

        if entry['event_id'] not in all_event_ids:
            logger.info(f'Completing non-existant event-task {task_id}')
            api.items.complete(task_id)
            api.commit()
            db.remove(Query().event_id == event_id)
            continue  # Skip updating as the event is over

        if clear_unattached_task(event, task_id, entry.get('due_string')):
            continue  # Skip updating as the task was deleted

        update_task_name(event, task_id)
        update_task_note(event, note_id)

    logger.info('Commiting final changes...')
    api.commit()


if __name__ == '__main__':
    cf = Configs()

    if cf.keep_running:
        killer = GracefulKiller()
        while not killer.kill_now:
            cf.refresh_calendar()
            cf.todoist_api.sync()

            cf.project_id = fetch_project_id()
            cf.label_id = fetch_label_id()

            main()
            sleep(cf.run_every)
    else:
        cf.refresh_calendar()
        cf.todoist_api.sync()

        cf.project_id = fetch_project_id()
        cf.label_id = fetch_label_id()

        main()
