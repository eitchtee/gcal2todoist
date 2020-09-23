from time import sleep

import todoist
import os
import yaml
import signal
from gcsa.google_calendar import GoogleCalendar


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
        self.run_every = data.get('run_every')


def fetch_project(api_obj):
    matching_projects = [project for project in api_obj.state['projects'] if
                         project['name'] == Configs().project]
    if len(matching_projects) >= 1:
        proj_id = matching_projects[0]['id']
    else:
        new_project = api_obj.projects.add(Configs().project)
        api_obj.commit()
        proj_id = new_project['id']

    return proj_id


def fetch_label(api_obj):
    matching_labels = [label for label in api_obj.state['labels'] if
                       label['name'] == Configs().label]
    if len(matching_labels) >= 1:
        r_label_id = matching_labels[0]['id']
    else:
        new_label = api_obj.labels.add(Configs().label)
        api_obj.commit()
        r_label_id = new_label['id']

    return r_label_id


def add_event(api_obj, task, date, description, location, label_id, proj_id):
    task = "* 🗓️ **" + task + '**'
    item = api_obj.items.add(content=task,
                             project_id=proj_id,
                             labels=[label_id],
                             due={'date': str(date),
                                  'is_recurring': False,
                                  'lang': 'pt',
                                  'timezone': None})
    api_obj.notes.add(item_id=item['id'],
                      content=location) if location else None
    api_obj.notes.add(item_id=item['id'],
                      content=description) if description else None


def clear_events(api_obj, proj_id, lab_id):
    for task in api_obj.state['items']:
        if task['project_id'] == proj_id and lab_id in task['labels']:
            print(task['labels'])
            item = api_obj.items.get_by_id(task['id'])
            item.delete()
    api_obj.commit()


def main():
    api = todoist.TodoistAPI(Configs().token)
    api.sync()
    project_id = fetch_project(api)
    label_id = fetch_label(api)

    clear_events(api, project_id, label_id)

    calendar = GoogleCalendar(credentials_path=os.path.join(
        os.path.dirname(__file__), '.credentials', 'credentials.json'))

    for event in calendar:
        add_event(api, event.summary, event.start, event.description,
                  event.location, label_id, project_id)

    api.commit()


if __name__ == '__main__':
    killer = GracefulKiller()
    while not killer.kill_now:
        main()
        sleep(Configs().run_every)
