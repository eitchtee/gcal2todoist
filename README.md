<!--
<div align="center">
    <img alt="BitcoinWalletBot Example" title="BitcoinWalletBot" src="./.github/images/header.png" />
</div>-->

<h3 align="center">Google Calendar 2 Todoist</h3>
<p align="center">A one-way sync between Google Calendar events and Todoist.</p>

---

## What's this?
This script aims to mimick TickTick's Google Calendar sync.


### End Result
<div align="center">
    <img alt="Gcal2Todoist Example" title="End result" src="./.github/images/end_result.png" />
</div>

Tasks created by this script are uncompletable and preceded by "🗓️", having the event start date as their due date, they are also added to their own project and assigned a custom label. Also a comment is added to each task with the events location and description.

Multiple day events get a task for each day, with older tasks getting completed each day.

Once an event is over, all tasks related to it will be automatically completed.

## Getting started

You will need:
1. Python 3.6+
1. A ``clone`` of this repo
1. Run ``pip install -r requirements.txt``
1. **.credentials** folder on the root of the script folder with your Google Calendar authentication files
1. Configure **configs.yml**
1. Run ``gcal2todoist.py``

### .credentials
Please refer to [Google Calendar Simple API's docs](https://google-calendar-simple-api.readthedocs.io/en/latest/getting_started.html#credentials) for a guide on how to create the ``.credentials`` folder

### configs.yml
```yaml
todoist_api_token: "YOUR_TODOIST_TOKEN_GENERATED_ON_THE_WEB_CLIENT"
default_project: "Events" # Name of the project you want your tasks to get added to. It will be created if it doesn't exist.
label: "Event" # Custom label to be added to each task created by the script.


keep_running: false # If false, script will run only once. Else it will run every n seconds
run_every: 600 # seconds

calendars: # Add here the id of as many calendars as you want to sync.
  - "primary" # your main calendar

log_level: "INFO" # Currently only INFO level logs are avaliable.

completed_label: A_LABEL_ID # Add this label to a event to complete it before it's given time (useful for full day 
  events)
```

## Contributing
Due to the lack of a start and end date on Todoist tasks, syncing with Google Calendar poses an interesting challenge, if you feel like you can improve this script please open an issue or a pull request, they are very much welcome.

*For major changes, please open an issue first to discuss what you would like to change.*

## License
[MIT](https://choosealicense.com/licenses/mit/)