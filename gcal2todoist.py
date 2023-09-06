import signal
from time import sleep
import datetime
import os
import logging

from requests import HTTPError
from todoist_api_python.api import TodoistAPI
from gcsa.google_calendar import GoogleCalendar
from gcsa.event import Event
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


class Calendar:
    def __init__(self, todoist_project_id, gcal_id):
        self.todoist_project_id = todoist_project_id
        self.gcal_id = gcal_id
        self.events = []


def generate_date_range(event: Event):
    date_range = []

    start = 0  # Range start
    end = (event.end - event.start).days  # Range end

    if end >= 1:
        end += 1  # Catch multi-day events

    for x in range(start, end):
        if x == start:
            start_date = event.start + datetime.timedelta(days=x)
        else:
            start_date = event.start + datetime.timedelta(days=x)
            if type(event.start) == datetime.datetime:
                # if a multi-day event start and end times, set the start time to midnight on the second day forward
                start_date = start_date.replace(hour=0, minute=0, second=0)

        if start_date >= event.end:
            continue

        duration = event.end - start_date
        duration = int(duration.total_seconds() / 60)

        if duration > 1440:  # Todoist tasks has a maximum duration of 24 hours
            duration = None

        date_range.append((start_date, duration, x))

    if not date_range:
        duration = event.end - event.start
        duration = int(duration.total_seconds() / 60)
        if duration >= 1440:  # Todoist tasks has a maximum duration of 24 hours
            duration = None
        date_range.append((event.start, duration, 0))

    return date_range


def is_event_on_db(db, event_id, date_string: str, index: int):
    return (
        True
        if db.search(
            (Query().event_id == event_id)
            & (Query().due_string == str(date_string))
            & (Query().index == index)
        )
        else None
    )


def get_event_on_db(db, event_id, date_string: str, index: int):
    return db.search(
        (Query().event_id == event_id)
        & (Query().due_string == str(date_string))
        & (Query().index == index)
    )[0]


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

        self.days_to_fetch = None

        self.todoist_token = None
        self.todoist = None

        self.calendars = []
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
        self.days_to_fetch = data.get("days_to_fetch")

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

            self.calendars.append(
                Calendar(todoist_project_id=project, gcal_id=gcal_calendar_id)
            )

    def refresh_calendar(self):
        self.events = []

        for calendar in self.calendars:
            gc = GoogleCalendar(
                calendar.gcal_id,
                credentials_path=os.path.join(
                    os.path.dirname(__file__), ".credentials", "credentials.json"
                ),
            ).get_events(
                time_min=datetime.datetime.today(),
                time_max=datetime.datetime.today()
                + datetime.timedelta(
                    days=self.days_to_fetch,
                ),
                single_events=True,
            )

            logger.info(f'Getting calendar: "{calendar.gcal_id}"')

            calendar.events += list(gc)

    def run(self):
        for calendar in self.calendars:
            existing_tasks = self.todoist.get_tasks(
                project_id=calendar.todoist_project_id
            )

            for event in calendar.events:
                date_range = generate_date_range(event)
                for date, duration, index in date_range:
                    if (
                        type(date) is datetime.date
                        and date < datetime.datetime.today().date()
                    ) or (
                        type(date) is datetime.datetime
                        and date < datetime.datetime.now().replace(tzinfo=date.tzinfo)
                    ):
                        continue  # Skip event dates older than today

                    logger.info(f"Handling task: {event.summary} [{index}]")

                    if is_event_on_db(
                        db=self.db, event_id=event.id, date_string=date, index=index
                    ):
                        event_on_db = get_event_on_db(
                            db=self.db, event_id=event.id, date_string=date, index=index
                        )

                        event_on_todoist = list(
                            filter(
                                lambda obj: obj.id == event_on_db.get("task_id"),
                                existing_tasks,
                            )
                        )
                        event_on_todoist = (
                            event_on_todoist[0] if event_on_todoist else None
                        )

                        task = Task(
                            event=event,
                            gt=self,
                            gcal_id=calendar.gcal_id,
                            todoist_project_id=calendar.todoist_project_id,
                            date=date,
                            duration=duration,
                            index=index,
                            task_on_todoist=event_on_todoist,
                            task_on_db=event_on_db,
                        )

                    else:
                        task = Task(
                            event=event,
                            gt=self,
                            gcal_id=calendar.gcal_id,
                            todoist_project_id=calendar.todoist_project_id,
                            date=date,
                            duration=duration,
                            index=index,
                        )
                # self.clear_yesterday_tasks(event)

        # self.clear_non_existant_task()


class Task:
    def __init__(
        self,
        event: Event,
        date,
        duration: int,
        index: int,
        gt: Gcal2Todoist,
        gcal_id: str,
        todoist_project_id: str,
        task_on_todoist=None,
        task_on_db=None,
    ):
        self.event = event
        self.g2t = gt

        self.task_name = self.generate_task_name()
        self.note = self.generate_note()

        self.date = date
        self.duration = duration
        self.index = index

        self.gcal_id = gcal_id
        self.todoist_project_id = todoist_project_id

        self.task_on_todoist = task_on_todoist
        self.task_on_db = task_on_db

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

        if hangout_link:
            note.append(f"📞 {hangout_link}")
        if location:
            note.append(f"📍 {location}")
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
            note.append("")

        note = "\n\n".join(note).strip()

        return note

    def add_or_update(self):
        search = Query()

        event_atendees = {
            atendee.email: atendee.response_status for atendee in self.event.attendees
        }

        if type(self.date) is datetime.datetime:
            date = str(self.date.astimezone(datetime.timezone.utc))
            task_date = {"due_date": date}
        else:
            date = str(self.date)
            task_date = {
                "due_datetime": date,
            }

        if self.duration:
            task_date["duration"] = self.duration
            task_date["duration_unit"] = "minute"

        if not self.g2t.db.search(
            (search.event_id == self.event.id)
            & (search.due_string == str(self.date))
            & (search.index == self.index)
        ):  # Event does not exist on DB
            if not self.event.attendees or (
                self.event.attendees
                and self.gcal_id in event_atendees.keys()
                and event_atendees[self.gcal_id]
                in ["accepted", "needsAction", "tentative"]
            ):
                logger.info(f"Adding event task: {self.event.summary} [{self.index}]")
                try:
                    item = self.g2t.todoist.add_task(
                        content=self.task_name,
                        description=self.note,
                        project_id=self.todoist_project_id,
                        labels=[self.g2t.label],
                        **task_date,
                    )
                    self.task_on_todoist = item
                except HTTPError:
                    return

                self.g2t.db.insert(
                    {
                        "event_id": self.event.id,
                        "task_id": item.id,
                        "due_string": str(self.date),
                        "index": self.index,
                    }
                )
        else:
            search = Query()
            result = self.g2t.db.search(
                (search.event_id == self.event.id)
                & (search.due_string == str(self.date))
                & (search.index == self.index)
            )[0]
            task_id = result["task_id"]
            try:
                task_obj = self.g2t.todoist.get_task(task_id)

            except HTTPError as err:
                if err.response.status_code == 404:
                    task_obj = None
                else:
                    return

            if task_obj and (
                task_obj.content != self.task_name
                or task_obj.description != self.note
                or task_obj.duration
            ):
                item = self.g2t.todoist.update_task(
                    task_id=task_id,
                    description=self.note,
                    content=self.task_name,
                    project_id=self.todoist_project_id,
                    labels=[self.g2t.label],
                    **task_date,
                )

                self.g2t.db.update(
                    {
                        "event_id": self.event.id,
                        "task_id": item["id"],
                        "due_string": str(self.date),
                        "index": self.index,
                    },
                    (search.event_id == self.event.id)
                    & (search.due_string == str(self.date))
                    & (search.index == self.index),
                )

            if not task_obj:
                item = self.g2t.todoist.add_task(
                    content=self.task_name,
                    description=self.note,
                    project_id=self.todoist_project_id,
                    labels=[self.g2t.label],
                    **task_date,
                )

                self.g2t.db.update(
                    {
                        "event_id": self.event.id,
                        "task_id": item.id,
                        "due_string": str(self.date),
                        "index": self.index,
                    },
                    (search.event_id == self.event.id)
                    & (search.due_string == str(self.date))
                    & (search.index == self.index),
                )

            elif (
                self.g2t.completed_label in task_obj.labels
                and not task_obj.is_completed
            ):
                logger.info(f"Completing task by request: {self.event.summary}")
                self.g2t.todoist.close_task(task_id)

            if (
                self.event.attendees
                and self.gcal_id in event_atendees.keys()
                and event_atendees[self.gcal_id]
                not in ["accepted", "needsAction", "tentative"]
            ):
                logger.info(f"Deleting not accepted event task: {self.event.summary}")
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
