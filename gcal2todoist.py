import signal
from time import sleep
import datetime
import os

import todoist
from gcsa.google_calendar import GoogleCalendar
import yaml
from tinydb import TinyDB, Query


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

        self.token = data.get('todoist_api_token')
        self.project = data.get('default_project')
        self.label = data.get('label')
        self.keep_running = data.get('keep_running')
        self.run_every = data.get('run_every')
        self.calendars = data.get('calendars', ['primary'])

        self.todoist_api = todoist.TodoistAPI(self.token)
        self.calendar = []
        self.db = TinyDB('events.json')

        self.project_id = None
        self.label_id = None

    def refresh_calendar(self):
        for calendar in self.calendars:
            self.calendar += list(GoogleCalendar(calendar,
                                                 credentials_path=os.path.join(
                                                     os.path.dirname(__file__),
                                                     '.credentials',
                                                     'credentials.json')))


def fetch_project_id():
    matching_projects = [project for project
                         in cf.todoist_api.state['projects'] if
                         project['name'] == Configs().project]
    if len(matching_projects) >= 1:
        proj_id = matching_projects[0]['id']
    else:
        new_project = cf.todoist_api.projects.add(Configs().project)
        cf.todoist_api.commit()
        proj_id = new_project['id']

    return proj_id


def fetch_label_id():
    matching_labels = [label for label in cf.todoist_api.state['labels'] if
                       label['name'] == Configs().label]
    if len(matching_labels) >= 1:
        r_label_id = matching_labels[0]['id']
    else:
        new_label = cf.todoist_api.labels.add(Configs().label)
        cf.todoist_api.commit()
        r_label_id = new_label['id']

    return r_label_id


def get_due_date(event):
    date_range = [event.start + datetime.timedelta(days=x) for x in
                  range(0, (event.end - event.start).days)]

    if len(date_range) >= 2:
        due_date = {
            'string': f'todo dia começando {date_range[0]} até '
                      f'{date_range[-1]}',
            'is_recurring': True,
            'lang': 'pt',
            'timezone': None}
    else:
        due_date = {'string': str(event.start),
                    'is_recurring': False,
                    'lang': 'pt',
                    'timezone': None}

    return due_date


def add_event(event):
    api = cf.todoist_api
    task = "* 🗓️ **" + event.summary + '**'

    item = api.items.add(content=task,
                         project_id=cf.project_id,
                         labels=[cf.label_id],
                         due=get_due_date(event))

    if event.location:
        new_note = api.notes.add(item_id=item['id'],
                                 content=event.location)
        note_location = new_note
    else:
        note_location = {'id': None}

    if event.description:
        new_note = api.notes.add(item_id=item['id'],
                                 content=event.description)
        note_description = new_note
    else:
        note_description = {'id': None}

    return {'item': item,
            'note_location': note_location,
            'note_description': note_description,
            'event_id': event.id}


def main():
    db = cf.db
    todoist_api = cf.todoist_api

    # Create db entry for all event_ids not already on
    items_n_notes = []
    for event in cf.calendar:
        if not db.search(Query().event_id == event.id):
            items_n_notes.append(add_event(event))

    todoist_api.commit()

    db.insert_multiple({'event_id': x['event_id'],
                        'task_id': x['item']['id'],
                        'note_ids': [x['note_location']['id'],
                                     x['note_description']['id']]} for x in
                       items_n_notes)

    all_event_ids = [x.id for x in cf.calendar]
    for entry in db:
        # Skip checking if a event occurs during multiple days as it will be
        # handled elsewhere
        # TO-DO: Implement this handling
        if isinstance(entry['task_id'], list):
            continue

        # Remove old entries and complete tasks
        if entry['event_id'] not in all_event_ids:
            todoist_api.items.complete(entry.get('task_id'))
            db.remove(Query().event_id == entry['event_id'])
            continue  # Skip updating as the event is over

        event = [x for x in cf.calendar if
                 x.id == entry['event_id']][0]

        # Re-add missing task_id-event
        if not todoist_api.items.get(entry['task_id']):
            items_n_notes = add_event(event)
            todoist_api.commit()
            db.insert({'event_id': items_n_notes['event_id'],
                       'task_id': items_n_notes['item']['id'],
                       'note_ids': [items_n_notes['note_location']['id'],
                                    items_n_notes['note_description']['id']]})

        # Update task_id names
        task_name = "* 🗓️ **" + event.summary + '**'
        if todoist_api.items.get(
                entry['task_id'])['item']['content'] != task_name:
            todoist_api.items.update(entry['task_id'], content=task_name)

        # Update due dates
        if todoist_api.items.get(
                entry['task_id'])['item']['due']['string'] != \
                get_due_date(event)['string']:
            todoist_api.items.update(entry['task_id'],
                                     due={'string': str(event.start)})

        # Update and delete notes
        for i, note_id in enumerate(entry['note_ids']):
            if note_id and todoist_api.notes.get(note_id):
                note = todoist_api.notes.get(note_id)

                if i == 0:
                    if event.location and \
                            event.location != note['note']['content']:
                        todoist_api.notes.update(note_id,
                                                 content=event.location)
                    else:
                        todoist_api.notes.delete(note_id)
                elif i == 1:
                    if event.description and \
                            event.description != note['note']['content']:
                        todoist_api.notes.update(note_id,
                                                 content=event.description)
                    else:
                        todoist_api.notes.delete(note_id)

    todoist_api.commit()


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

    print('Goodbye!')
