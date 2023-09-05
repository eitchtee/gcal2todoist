import signal
from time import sleep
import datetime
import os
import logging

from requests import HTTPError
from todoist_api_python.api import TodoistAPI
from gcsa.google_calendar import GoogleCalendar
import gcsa.event
import yaml
from tinydb import TinyDB, Query
from dateutil.parser import parse

from markdownify import markdownify


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

    def exit_gracefully(self, *args):
        self.kill_now = True


class Gcal2Todoist:
    def __init__(self):
        self.mother_project_name = None
        self.mother_project_id = None

        self.label = None
        self.keep_running = None
        self.run_every = None

        self.task_prefix = None
        self.task_suffix = None
        self.completed_label = None

        self.todoist_token = None
        self.todoist = None

        self.calendar_projects = []
        self.events = []
        self.db = None

        self.configure()
        self.fetch_mother_project_id()
        self.get_calendars()
        self.refresh_calendar()

    def configure(self):
        configs_path = os.path.join(os.path.dirname(__file__), "configs.yml")

        with open(configs_path, encoding="utf8") as file:
            data = yaml.load(file, Loader=yaml.FullLoader)

        log_level = data.get("log_level", "INFO")
        logger.setLevel(log_level)

        self.todoist_token = data.get("todoist_api_token")
        self.mother_project_name = data.get("default_project")
        self.label = data.get("label")
        self.keep_running = data.get("keep_running")
        self.run_every = data.get("run_every")
        self.task_prefix = data.get("task_prefix", "* 🗓️ ")
        self.task_suffix = data.get("task_suffix", "")
        self.completed_label = data.get("completed_label")

        self.db = TinyDB(os.path.join(os.path.dirname(__file__), "events.json"))

        if not self.todoist_token:
            raise Exception("Todoist token not set.")

        self.todoist = TodoistAPI(self.todoist_token)

    def fetch_mother_project_id(self):
        logger.info("Fetching mother project-id")
        matching_projects = [
            project
            for project in self.todoist.get_projects()
            if project.name == self.mother_project_name
        ]
        if len(matching_projects) >= 1:
            proj_id = matching_projects[
                0
            ].id  # Always return the first project listed by Todoist
        else:
            new_project = self.todoist.add_project(self.mother_project_name)
            proj_id = new_project.id

        logger.info(f"Project-id found: {proj_id}")

        self.mother_project_id = proj_id

    def get_calendars(self):
        for project in [
            x.id
            for x in self.todoist.get_projects()
            if x.parent_id == self.mother_project_id and x.comment_count >= 1
        ]:
            gcal_calendar_id = self.todoist.get_comments(project_id=project)[0].content

            self.calendar_projects.append(
                {"gcal_id": gcal_calendar_id, "todoist_id": project}
            )

    def refresh_calendar(self):
        self.events = []

        for calendar in self.calendar_projects:
            gc = GoogleCalendar(
                calendar["gcal_id"],
                credentials_path=os.path.join(
                    os.path.dirname(__file__), ".credentials", "credentials.json"
                ),
            ).get_events(
                time_min=datetime.datetime.today(),
                time_max=datetime.datetime.today()
                + datetime.timedelta(
                    days=7,
                ),
                single_events=True,
            )

            calendar_project = calendar["todoist_id"]
            logger.info(f'Getting calendar: "{calendar}"')
            events = list(gc)

            for event in events:
                setattr(event, "calendar_project", calendar_project)
                setattr(event, "calendar_name", calendar["gcal_id"])

            self.events += events

    def clear_yesterday_tasks(self, event):
        api = self.todoist

        tasks = self.db.search(Query().event_id == event.id)

        for i, task in enumerate(tasks):
            due_date = parse(task["due_string"])
            if due_date.date() < datetime.datetime.today().date():
                logger.info(f'Removing stale "{i}" task from "{event.summary}"')
                api.close_task(task["task_id"])
                self.db.remove(Query().task_id == task["task_id"])

    def clear_unattached_task(self, event, task_id, due_date):
        api = self.todoist

        dates = [
            str(x) for x, _, _ in Task(event=event, gt=self).generate_desired_dates()
        ]

        if due_date not in dates:
            logger.info(f"Removing unattached task from {event.summary}")
            api.delete_task(task_id)
            self.db.remove(
                (Query().task_id == task_id) & (Query().due_string == due_date)
            )
            return True

        return False

    def clear_non_existant_task(self):
        all_event_ids = [x.id for x in self.events]
        for entry in self.db:
            task_id = entry.get("task_id")
            note_id = entry.get("note_id")
            event_id = entry.get("event_id")

            if entry["event_id"] not in all_event_ids:
                logger.info(f"Deleting non-existant event-task {task_id}")
                self.todoist.delete_task(task_id)

                self.db.remove(Query().event_id == event_id)
                continue  # Skip updating as the event is over

            event = [x for x in self.events if x.id == event_id][0]

            if self.clear_unattached_task(event, task_id, entry.get("due_string")):
                continue  # Skip updating as the task was deleted

    def run(self):
        for event in self.events:
            Task(event=event, gt=self).add()
            self.clear_yesterday_tasks(event)

        self.clear_non_existant_task()


class Task:
    def __init__(self, event: gcsa.event, gt: Gcal2Todoist):
        self.event = event
        self.g2t = gt

        self.task_name = self.generate_task_name()
        self.note = self.generate_note()
        self.dates = self.generate_desired_dates()

    def generate_task_name(self):
        return (
            f"{self.g2t.task_prefix}{self.event.summary.strip()}{self.g2t.task_suffix}"
        )

    def generate_note(self):
        note = []
        location = self.event.location
        description = self.event.description
        hangout_link = self.event.other.get("hangoutLink")
        attendees = self.event.attendees
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
            note.append(f"📝 {markdownify(description)}")
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

        note = "\n\n".join(note).strip()

        return note

    def generate_desired_dates(self):
        date_range = []

        start = 0
        end = (self.event.end - self.event.start).days

        if end >= 1:
            end += 1

        for x in range(start, end):
            if x == start:
                start_date = self.event.start + datetime.timedelta(days=x)
            else:
                start_date = self.event.start + datetime.timedelta(days=x)
                if type(self.event.start) == datetime.datetime:
                    start_date = start_date.replace(hour=0, minute=0, second=0)

            duration = self.event.end - start_date
            duration = int(duration.total_seconds() / 60)

            if duration > 1440:
                duration = None

            date_range.append((start_date, duration, x))

        if not date_range:
            duration = self.event.end - self.event.start
            duration = int(duration.total_seconds() / 60)
            if duration >= 1440:
                duration = None
            date_range.append((self.event.start, duration, 0))

        return date_range

    def add(self):
        logger.info(f"Handling task: {self.event.summary}")

        event_atendees = {
            atendee.email: atendee.response_status for atendee in self.event.attendees
        }

        for date_, duration, i in self.dates:
            due_date = parse(str(date_))

            if type(date_) is datetime.datetime:
                date = str(date_.astimezone(datetime.timezone.utc))
                task_date = {"due_date": date}
            else:
                date = str(date_)
                task_date = {
                    "due_datetime": date,
                }

            if duration:
                task_date["duration"] = duration
                task_date["duration_unit"] = "minute"

            if due_date.date() < datetime.datetime.today().date():
                continue

            search = Query()
            if not self.g2t.db.search(
                (search.event_id == self.event.id)
                & (search.due_string == str(date_))
                & (search.index == i)
            ):
                if not self.event.attendees or (
                    self.event.attendees
                    and self.event.calendar_name in event_atendees.keys()
                    and event_atendees[self.event.calendar_name]
                    in ["accepted", "needsAction", "tentative"]
                ):
                    logger.info(f"Adding event task: {self.event.summary}")
                    try:
                        item = self.g2t.todoist.add_task(
                            content=self.task_name,
                            project_id=self.event.calendar_project,
                            labels=[self.g2t.label],
                            **task_date,
                        )

                        comment = self.g2t.todoist.add_comment(
                            content=self.note, task_id=item.id
                        )
                    except HTTPError:
                        continue

                    self.g2t.db.insert(
                        {
                            "event_id": self.event.id,
                            "task_id": item.id,
                            "note_id": comment.id,
                            "due_string": str(date_),
                            "index": i,
                        }
                    )
            else:
                search = Query()
                result = self.g2t.db.search(
                    (search.event_id == self.event.id)
                    & (search.due_string == str(date_))
                    & (search.index == i)
                )[0]
                task_id = result["task_id"]
                note_id = result["note_id"]
                try:
                    task_obj = self.g2t.todoist.get_task(task_id)

                except HTTPError as err:
                    if err.response.status_code == 404:
                        task_obj = None
                    else:
                        continue

                if task_obj:
                    item = self.g2t.todoist.update_task(
                        task_id=task_id,
                        content=self.task_name,
                        project_id=self.event.calendar_project,
                        labels=[self.g2t.label],
                        **task_date,
                    )

                    comment = self.g2t.todoist.update_comment(
                        comment_id=note_id, content=self.note
                    )

                    self.g2t.db.update(
                        {
                            "event_id": self.event.id,
                            "task_id": item.id,
                            "note_id": comment.id,
                            "due_string": str(date_),
                            "index": i,
                        },
                        (search.event_id == self.event.id)
                        & (search.due_string == str(date_))
                        & (search.index == i),
                    )

                if not task_obj:
                    item = self.g2t.todoist.add_task(
                        content=self.task_name,
                        project_id=self.event.calendar_project,
                        labels=[self.g2t.label],
                        **task_date,
                    )

                    comment = self.g2t.todoist.add_comment(
                        content=self.note, task_id=item.id
                    )

                    self.g2t.db.update(
                        {
                            "event_id": self.event.id,
                            "task_id": item.id,
                            "note_id": comment.id,
                            "due_string": str(date_),
                            "index": i,
                        },
                        (search.event_id == self.event.id)
                        & (search.due_string == str(date_))
                        & (search.index == i),
                    )

                elif (
                    self.g2t.completed_label in task_obj.labels
                    and not task_obj.is_completed
                ):
                    logger.info(f"Completing task by request: {self.event.summary}")
                    self.g2t.todoist.close_task(task_id)

                if (
                    self.event.attendees
                    and self.event.calendar_name in event_atendees.keys()
                    and event_atendees[self.event.calendar_name]
                    not in ["accepted", "needsAction", "tentative"]
                ):
                    logger.info(
                        f"Deleting not accepted event task: {self.event.summary}"
                    )
                    self.g2t.todoist.delete_task(task_id)
                    self.g2t.db.remove(
                        (search.task_id == task_id) & (search.event_id == self.event.id)
                    )


if __name__ == "__main__":
    killer = GracefulKiller()
    while not killer.kill_now:
        g2t = Gcal2Todoist()

        try:
            g2t.run()
        except Exception as e:
            logger.error(e, exc_info=True)

        if not killer.kill_now and g2t.keep_running and g2t.run_every:
            logger.info(f"Running again in {g2t.run_every} seconds...")
            sleep(g2t.run_every)
        else:
            logger.info(f"Finishing...")
            break
