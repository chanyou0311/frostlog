"""frostlog-notifier: the data application that posts Slack digests.

It reads only the tables and events of ``contracts/semantic.odcs.yaml`` and keeps
its own record of what it has already posted, so that Pub/Sub redelivering an
event never produces a second message.
"""
