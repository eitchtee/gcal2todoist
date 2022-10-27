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
logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(name)-12s %(levelname)-8s %(message)s")
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
        self.token = None
        self.project = None
        self.label = None
        self.keep_running = None
        self.run_every = None
        self.calendars = None
        self.task_prefix = None
        self.task_suffix = None
        self.completed_label = None

        self.get_configs()

        self.todoist_api = todoist.TodoistAPI(self.token)
        self.calendar = []
        self.db = TinyDB(os.path.join(os.path.dirname(__file__), "events.json"))

        self.project_id = None
        self.label_id = None

    def refresh_calendar(self):
        self.calendar = []
        for calendar in self.calendars:
            gc = GoogleCalendar(
                calendar["id"],
                credentials_path=os.path.join(
                    os.path.dirname(__file__), ".credentials", "credentials.json"
                ),
            ).get_events(
                time_min=datetime.datetime.today(),
                time_max=datetime.datetime.today()
                + datetime.timedelta(
                    days=31,
                ),
                single_events=True,
            )

            calendar_project = fetch_project_id(calendar["name"], self.project_id)
            logger.info(f'Getting calendar: "{calendar}"')
            events = list(gc)

            for event in events:
                setattr(event, "calendar_project", calendar_project)
                setattr(event, "calendar_name", calendar["id"])

            self.calendar += events

    def get_configs(self):
        configs_path = os.path.join(os.path.dirname(__file__), "configs.yml")

        with open(configs_path, encoding="utf8") as file:
            data = yaml.load(file, Loader=yaml.FullLoader)

        log_level = data.get("log_level", "INFO")
        logger.setLevel(log_level)

        self.token = data.get("todoist_api_token")
        self.project = data.get("default_project")
        self.label = data.get("label")
        self.keep_running = data.get("keep_running")
        self.run_every = data.get("run_every")
        self.calendars = data.get("calendars", ["primary"])
        self.task_prefix = data.get("task_prefix", "* 🗓️ ")
        self.task_suffix = data.get("task_suffix", "")
        self.completed_label = data.get("completed_label")


def fetch_project_id(project_name, parent_id=None):
    logger.info("Fetching desired project-id")
    matching_projects = [
        project
        for project in cf.todoist_api.state["projects"]
        if project["name"] == project_name
    ]
    if len(matching_projects) >= 1:
        proj_id = matching_projects[0]["id"]
    else:
        new_project = cf.todoist_api.projects.add(project_name, parent_id=parent_id)
        cf.todoist_api.commit()
        proj_id = new_project["id"]

    logger.info(f"Project-id found: {proj_id}")
    return proj_id


def fetch_label_id(label_name):
    logger.info("Fetching desired label-id")
    matching_labels = [
        label for label in cf.todoist_api.state["labels"] if label["name"] == label_name
    ]
    if len(matching_labels) >= 1:
        r_label_id = matching_labels[0]["id"]
    else:
        new_label = cf.todoist_api.labels.add(label_name)
        cf.todoist_api.commit()
        r_label_id = new_label["id"]

    logger.info(f"Label-id found: {r_label_id}")
    return r_label_id


def generate_note(event):
    note = []
    location = event.location
    description = event.description
    hangout_link = event.other.get("hangoutLink")
    attendees = event.attendees
    attendee_status = {
        "accepted": "🟢",
        "declined": "🔴",
        "needsAction": "⚫",
        "tentative": "🟡",
    }

    if location:
        note.append(f"📍 {location}")
    if hangout_link:
        note.append(f"📞 {hangout_link}")
    if description:
        note.append(f"📝 {description}")
    if attendees:
        result = ["👥 Convidados:\n"]

        for attendee in attendees:
            display_line = [attendee_status[attendee.response_status]]

            if attendee.display_name:
                display_line.append(attendee.display_name)
            if attendee.email:
                display_line.append(attendee.email)

            if len(display_line) >= 2:
                result.append(" - ".join(display_line) + "\n")
        note.append("".join(result))
    if len(note) == 0:
        note.append("❌")

    note = "\n\n".join(note)

    return note


def generate_task_name(title):
    return f"{cf.task_prefix}{title.strip()}{cf.task_suffix}"


def generate_desired_dates(event):
    date_range = [
        str(event.start + datetime.timedelta(days=x))
        for x in range(0, (event.end - event.start).days)
    ]

    if not date_range:
        date_range.append(str(event.start))

    return date_range


def add_task(event):
    api = cf.todoist_api

    task = generate_task_name(event.summary)
    note = generate_note(event)
    dates = generate_desired_dates(event)

    logger.info(f"Handling task: {event.summary}")

    event_atendees = {
        atendee.email: atendee.response_status for atendee in event.attendees
    }

    for date in dates:
        due_date = parse(date)
        if due_date.date() < datetime.datetime.today().date():
            continue

        search = Query()
        if not cf.db.search(
            (search.event_id == event.id) & (search.due_string == date)
        ):
            if not event.attendees or (
                event.attendees
                and event.calendar_name in event_atendees.keys()
                and event_atendees[event.calendar_name]
                in ["accepted", "needsAction", "tentative"]
            ):
                logger.info(f"Adding event task: {event.summary}")
                item = api.add_item(
                    content=task,
                    project_id=event.calendar_project,
                    labels=[cf.label_id],
                    date_string=str(date),
                    note=note,
                )

                item = api.items.get(item["id"])

                api.commit()

                cf.db.insert(
                    {
                        "event_id": event.id,
                        "task_id": item["item"]["id"],
                        "note_id": item["notes"][0]["id"],
                        "due_string": date,
                    }
                )
        else:
            search = Query()
            result = cf.db.search(
                (search.event_id == event.id) & (search.due_string == date)
            )[0]
            task_id = result["task_id"]
            task_obj = api.items.get_by_id(task_id)

            if not task_obj:
                item = api.add_item(
                    content=task,
                    project_id=event.calendar_project,
                    labels=[cf.label_id],
                    date_string=str(date),
                    note=note,
                )

                api.commit()

                item = api.items.get(item["id"])

                cf.db.update(
                    {
                        "event_id": event.id,
                        "task_id": item["item"]["id"],
                        "note_id": item["notes"][0]["id"],
                        "due_string": date,
                    },
                    (search.event_id == event.id) & (search.due_string == date),
                )

            elif cf.completed_label in task_obj["labels"] and not task_obj["checked"]:
                logger.info(f"Completing task by request: {event.summary}")
                api.items.complete(task_id)
                api.commit()

            if (
                event.attendees
                and event.calendar_name in event_atendees.keys()
                and event_atendees[event.calendar_name]
                not in ["accepted", "needsAction", "tentative"]
            ):
                logger.info(f"Deleting not accepted event task: {event.summary}")
                api.items.delete(task_id)
                cf.db.remove(
                    (search.task_id == task_id) & ((search.event_id == event.id))
                )
                api.commit()


def clear_yesterday_tasks(event):
    api = cf.todoist_api

    tasks = cf.db.search(Query().event_id == event.id)

    for i, task in enumerate(tasks):
        due_date = parse(task["due_string"])
        if due_date.date() < datetime.datetime.today().date():
            logger.info(f'Removing stale "{i}" task from "{event.summary}"')
            api.items.complete(task["task_id"])
            api.commit()
            cf.db.remove(Query().task_id == task["task_id"])


def clear_unattached_task(event, task_id, due_date):
    api = cf.todoist_api

    dates = generate_desired_dates(event)

    if due_date not in dates:
        logger.info(f"Removing unattached task from {event.summary}")
        api.items.delete(task_id)
        api.commit()
        cf.db.remove((Query().task_id == task_id) & (Query().due_string == due_date))
        return True

    return False


def update_task_name(event, task_id):
    title = generate_task_name(event.summary)
    item = cf.todoist_api.items.get_by_id(task_id)
    cur_title = item["content"] if item else None
    if cur_title and cur_title != title:
        logger.info(f'Updating task name for: "{event.summary}"')
        cf.todoist_api.items.update(task_id, content=title)


def update_task_note(event, note_id):
    api = cf.todoist_api
    note_content = generate_note(event)

    note = api.notes.get_by_id(note_id)
    cur_content = note["content"] if note else None
    if cur_content and cur_content != note_content:
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
        task_id = entry.get("task_id")
        note_id = entry.get("note_id")
        event_id = entry.get("event_id")

        if entry["event_id"] not in all_event_ids:
            logger.info(f"Deleting non-existant event-task {task_id}")
            try:
                api.items.delete(task_id)
                api.commit()
            except todoist.api.SyncError:
                pass
            db.remove(Query().event_id == event_id)
            continue  # Skip updating as the event is over

        event = [x for x in cf.calendar if x.id == event_id][0]

        if clear_unattached_task(event, task_id, entry.get("due_string")):
            continue  # Skip updating as the task was deleted

        update_task_name(event, task_id)
        update_task_note(event, note_id)

    logger.info("Commiting final changes...")
    api.commit()


if __name__ == "__main__":
    cf = Configs()

    if cf.keep_running:
        killer = GracefulKiller()
        while not killer.kill_now:
            try:
                cf.get_configs()

                cf.project_id = fetch_project_id(cf.project)
                cf.label_id = fetch_label_id(cf.label)

                cf.refresh_calendar()
                cf.todoist_api.sync()

                main()
            except Exception as e:
                logger.error(e, exc_info=True)
            logger.info(f"Running again in {cf.run_every} seconds...")
            sleep(cf.run_every)
    else:
        cf.get_configs()

        cf.project_id = fetch_project_id(cf.project)
        cf.label_id = fetch_label_id(cf.label)

        cf.refresh_calendar()
        cf.todoist_api.sync()

        main()
