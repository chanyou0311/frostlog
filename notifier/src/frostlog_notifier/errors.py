"""The one distinction the endpoints make between failures."""


class Transient(Exception):
    """A failure that a later delivery of the same message may well survive.

    BigQuery or Slack being unavailable or rate limiting; the endpoint answers
    500 so that Pub/Sub retries. Anything else is a defect: the message is
    acknowledged and reported, because redelivering it would only repeat it.
    """
