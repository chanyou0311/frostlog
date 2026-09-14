"""frostlog-notifier: the data application that posts Slack digests.

It reads only the tables of ``contracts/semantics.odcs.yaml`` and the events of
``contracts/signals.odcs.yaml``, and remembers nothing between runs: every summary
is a picture of a window ending at the moment its job fires.
"""
