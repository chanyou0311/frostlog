"""Which GCP project this job is running in, and where its topic is.

Neither is configuration with a sensible default: the project is whatever the
credentials belong to, and the topic's full name is built from it.
"""


def resolve_project(override: str | None = None) -> str:
    """The GCP project to work in: the override, else the one ADC belong to."""
    if override:
        return override
    import google.auth

    _, project = google.auth.default()
    if not project:
        raise RuntimeError("no project in the ambient credentials; set FROSTLOG_GCP_PROJECT")
    return str(project)


def topic_path(project: str, topic: str) -> str:
    """Full Pub/Sub topic name from the short name Terraform passes."""
    if topic.startswith("projects/"):
        return topic
    return f"projects/{project}/topics/{topic}"
