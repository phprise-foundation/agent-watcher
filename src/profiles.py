"""Loading and rendering of personality profiles (--profile).

A profile is a YAML file describing the AI behavior:

    profile_name: "Socratic Strategist"
    style_description: "..."
    psychological_attributes:
      enthusiasm: 4
      ...
    communication:
      tone_of_voice: "..."
      ...
    cultural_background:
      ...
    behavior_guidelines: []
    biases_and_restrictions: []

The reference passed in `--profile` can be:
  - a predefined name (e.g. "socratic" -> <profiles_dir>/socratic.yaml)
  - a path mounted in the container (e.g. "/profiles/my-profile.yaml")
"""
import os
import pathlib

import yaml


def _dir():
    return pathlib.Path(os.environ.get("AW_PROFILES_DIR", "/app/profiles"))


_ATTRS = [
    "enthusiasm",
    "formality",
    "confidence",
    "methodicalness",
    "criticality",
    "empathy",
    "adaptability",
]


def _resolve(ref):
    p = pathlib.Path(ref)
    if p.exists():
        return p
    if ref.endswith(".yaml") or ref.endswith(".yml"):
        cand = _dir() / ref
    else:
        cand = _dir() / f"{ref}.yaml"
        if not cand.exists():
            cand = _dir() / f"{ref}.yml"
    if cand.exists():
        return cand
    raise FileNotFoundError(f"profile not found: {ref}")


def load_profile(ref):
    path = _resolve(ref)
    data = yaml.safe_load(path.read_text()) or {}
    return data


def _bullet(title, value):
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return f"- {title}: {value}"


def render(data):
    lines = []
    name = data.get("profile_name") or "Profile"
    lines.append(f"## {name}")
    if data.get("style_description"):
        lines.append(f"\n{data['style_description']}")

    attrs = data.get("psychological_attributes") or {}
    if attrs:
        lines.append("\n## Psychological attributes (scale 0-10)")
        for key in _ATTRS:
            if key in attrs:
                lines.append(f"- {key.replace('_', ' ')}: {attrs[key]}")

    com = data.get("communication") or {}
    if com:
        lines.append("\n## Communication")
        if com.get("tone_of_voice"):
            lines.append(f"- Tone of voice: {com['tone_of_voice']}")
        if com.get("keywords"):
            lines.append(_bullet("Keywords", com["keywords"]))
        if com.get("forbidden_expressions"):
            lines.append(_bullet("Forbidden expressions", com["forbidden_expressions"]))
        if com.get("emoji_usage"):
            lines.append(f"- Emoji usage: {com['emoji_usage']}")

    background = data.get("cultural_background") or {}
    if background:
        lines.append("\n## Cultural background and archetype")
        for key, label in [
            ("inspiring_books", "Inspiring books"),
            ("movies_and_entertainment", "Movies/entertainment"),
            ("inspiring_anime", "Anime"),
            ("great_sagas", "Great sagas"),
            ("references_and_people", "References and people"),
            ("hobbies", "Hobbies"),
            ("music_and_rhythms", "Music and rhythms"),
        ]:
            if background.get(key):
                lines.append(_bullet(label, background[key]))
        if background.get("archetype"):
            lines.append(f"- Archetype: {background['archetype']}")

    guidelines = data.get("behavior_guidelines") or []
    if guidelines:
        lines.append("\n## Behavior guidelines")
        for d in guidelines:
            lines.append(f"- {d}")

    biases = data.get("biases_and_restrictions") or []
    if biases:
        lines.append("\n## Biases and restrictions")
        for v in biases:
            lines.append(f"- {v}")

    return "\n".join(lines).strip()
