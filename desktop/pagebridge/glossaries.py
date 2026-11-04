"""Named glossaries: read-only built-ins from the engine and the user's own in glossaries.json.

The text of each glossary keeps the user's lines; engine/glossary.py parses it at translation time.
The file format matches the Mac app's GlossaryLibrary.
"""
import csv
import io
import json
from pathlib import Path
import re
import uuid

from .core import AppError, atomic_file

BUILTIN_PREFIX = "builtin:"
MAX_TEXT = 100_000
MAX_NAME = 60
MAX_TERM = 200
MAX_GLOSSARIES = 200
MAX_FILE_BYTES = 2_000_000
HEADER = "# PageBridge 术语表："
LANGUAGE_HEADER = "# PageBridge 语言："
SEPARATOR = re.compile(r"\t|\s*=>\s*|\s*→\s*|\s*=\s*")
LANGUAGE = re.compile(r"[a-zA-Z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?")


def entry_count(text):
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def entries(text):
    """(line number, source, target) the way engine/glossary.py splits each line."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            parts = SEPARATOR.split(line, maxsplit=1)
            found.append((number, parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""))
    return found


def line(source, target):
    source, target = source.strip(), (target or "").strip()
    if not target:
        return source
    return source + ("\t" if "=" in source or "→" in source else " = ") + target


def checked_name(name):
    name = (name or "").strip() if isinstance(name, str) else ""
    return (name or "未命名术语表")[:MAX_NAME]


def checked_text(text):
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise AppError("术语表超过 100,000 字符，请删减后再保存。")
    return text


def checked_language(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not LANGUAGE.fullmatch(value):
        raise AppError("术语表的语言格式无效。")
    return value


class GlossaryStore:
    def __init__(self, root, builtin_directory):
        self.path = Path(root) / "glossaries.json"
        if self.path.is_symlink():
            raise AppError("术语表路径被重定向，未读取。")
        self.builtin = self._builtin(Path(builtin_directory))
        self.user, self.last_used = [], []
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if value.get("version") != 1:
                    raise ValueError()
                self.user = [dict(id=str(item["id"]), name=str(item["name"]), text=str(item["text"]),
                                  sourceLanguage=item.get("sourceLanguage"), targetLanguage=item.get("targetLanguage"))
                             for item in value["glossaries"]]
                self.last_used = [str(i) for i in value.get("lastUsed") or []]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                raise AppError("术语表文件损坏或版本未知，原文件已保留。") from None

    @staticmethod
    def _builtin(directory):
        try:
            index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = []
        for item in index.get("glossaries", []) if index.get("version") == 1 else []:
            if "/" in item["file"] or "\\" in item["file"]:
                continue
            try:
                text = (directory / item["file"]).read_text(encoding="utf-8")
            except OSError:
                continue
            items.append(dict(id=BUILTIN_PREFIX + item["id"], name=item["name"], text=text,
                              sourceLanguage=item.get("sourceLanguage"), targetLanguage=item.get("targetLanguage")))
        return items

    def _save(self):
        value = dict(version=1, lastUsed=self.last_used,
                     glossaries=[{k: g[k] for k in ("id", "name", "text", "sourceLanguage", "targetLanguage") if g.get(k) is not None}
                                 for g in self.user])
        atomic_file(self.path, lambda temporary: Path(temporary).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"))

    def find(self, identifier):
        return next((g for g in self.user + self.builtin if g["id"] == identifier), None) if identifier else None

    def _own(self, identifier):
        glossary = next((g for g in self.user if g["id"] == identifier), None)
        if glossary is None:
            raise AppError("内置术语表不能修改，请先复制。" if self.find(identifier) else "术语表已不存在。")
        return glossary

    @staticmethod
    def summary(glossary, builtin):
        return dict(id=glossary["id"], name=glossary["name"], builtin=builtin, entries=entry_count(glossary["text"]),
                    source_language=glossary.get("sourceLanguage"), target_language=glossary.get("targetLanguage"))

    def snapshot(self):
        return dict(glossaries=[self.summary(g, False) for g in self.user] + [self.summary(g, True) for g in self.builtin],
                    last_used=[i for i in self.last_used if self.find(i)])

    def get(self, identifier):
        glossary = self.find(identifier)
        if glossary is None:
            raise AppError("术语表已不存在。")
        return dict(self.summary(glossary, glossary in self.builtin), text=glossary["text"])

    def combined_text(self, identifiers):
        """Selected glossaries as one engine text: built-ins first, so the user's own entries win."""
        chosen = set(identifiers or ())
        return "\n".join(g["text"] for g in self.builtin + self.user if g["id"] in chosen)

    def expansion_terms(self, question, limit=20):
        """Source terms whose rendering appears in a question asked in the target language."""
        lowered, terms = (question or "").lower(), []
        for glossary in self.user + self.builtin:
            for _, source, target in entries(glossary["text"]):
                if len(target) >= 2 and target.lower() in lowered and source.lower() not in terms:
                    terms.append(source.lower())
                    if len(terms) >= limit:
                        return terms
        return terms

    def create(self, name, text="", source_language=None, target_language=None):
        if len(self.user) >= MAX_GLOSSARIES:
            raise AppError(f"最多保存 {MAX_GLOSSARIES} 个术语表。")
        glossary = dict(id=str(uuid.uuid4()), name=checked_name(name), text=checked_text(text),
                        sourceLanguage=checked_language(source_language), targetLanguage=checked_language(target_language))
        self.user.append(glossary)
        self._save()
        return self.get(glossary["id"])

    def duplicate(self, identifier):
        source = self.find(identifier)
        if source is None:
            raise AppError("术语表已不存在。")
        return self.create((source["name"] + " 副本")[:MAX_NAME], source["text"], source.get("sourceLanguage"), source.get("targetLanguage"))

    def update(self, identifier, name, text, source_language=None, target_language=None):
        glossary = self._own(identifier)
        glossary.update(name=checked_name(name), text=checked_text(text),
                        sourceLanguage=checked_language(source_language), targetLanguage=checked_language(target_language))
        self._save()
        return self.get(identifier)

    def add_entry(self, identifier, source, target):
        """Append one line; a later line for the same term replaces earlier ones in the engine."""
        if not isinstance(source, str) or not source.strip() or len(source) > MAX_TERM or (target and (not isinstance(target, str) or len(target) > MAX_TERM)):
            raise AppError("原文不能为空，原文和译文各最多 200 字符。")
        glossary = self._own(identifier)
        text = glossary["text"]
        glossary["text"] = checked_text(text + ("" if not text or text.endswith("\n") else "\n") + line(source, target) + "\n")
        self._save()
        return self.get(identifier)

    def delete(self, identifier):
        self._own(identifier)
        self.user = [g for g in self.user if g["id"] != identifier]
        self.last_used = [i for i in self.last_used if i != identifier]
        self._save()
        return self.snapshot()

    def set_last_used(self, identifiers):
        identifiers = list(identifiers or [])
        if identifiers != self.last_used:
            self.last_used = identifiers
            self._save()

    def import_file(self, path):
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError:
            raise AppError(f"无法读取 {path.name}。") from None
        if len(data) > MAX_FILE_BYTES:
            raise AppError(f"{path.name} 太大，术语表文件最多 2 MB。")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("gb18030")
            except UnicodeDecodeError:
                raise AppError(f"无法读取 {path.name}：请保存为 UTF-8 或 GB18030 文本。") from None
        name, languages = path.stem, (None, None)
        if path.suffix.lower() == ".csv":
            rows = list(csv.reader(io.StringIO(text)))
            if rows and rows[0] and rows[0][0].strip().lower() in ("原文", "source", "term"):
                rows = rows[1:]
            text = "\n".join(line(row[0], row[1] if len(row) > 1 else "") for row in rows if row and row[0].strip()) + "\n"
        else:
            # A file exported by PageBridge names itself and its languages in its first lines.
            text = text.replace("\r\n", "\n")
            while "\n" in text and (text.startswith(HEADER) or text.startswith(LANGUAGE_HEADER)):
                first, text = text.split("\n", 1)
                if first.startswith(HEADER):
                    name = first[len(HEADER):].strip()
                else:
                    parts = [p.strip() for p in first[len(LANGUAGE_HEADER):].split("→")]
                    if len(parts) == 2:
                        languages = tuple(None if p in ("", "*") or not LANGUAGE.fullmatch(p) else p for p in parts)
        return self.create(name, text.replace("\r\n", "\n"), *languages)

    def export_file(self, identifier, destination):
        glossary = self.find(identifier)
        if glossary is None:
            raise AppError("术语表已不存在。")
        destination = Path(destination)
        if not destination.is_absolute() or not destination.parent.is_dir():
            raise AppError("请选择有效的保存位置。")
        if destination.suffix.lower() == ".csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(["原文", "译文"])
            for _, source, target in entries(glossary["text"]):
                writer.writerow([source, target])
            # The BOM lets Excel open the Chinese text as UTF-8.
            content = "﻿" + buffer.getvalue()
        else:
            head = HEADER + glossary["name"] + "\n"
            if glossary.get("sourceLanguage") or glossary.get("targetLanguage"):
                head += LANGUAGE_HEADER + (glossary.get("sourceLanguage") or "*") + " → " + (glossary.get("targetLanguage") or "*") + "\n"
            body = glossary["text"]
            content = head + body + ("" if body.endswith("\n") else "\n")
        atomic_file(destination, lambda temporary: Path(temporary).write_text(content, encoding="utf-8", newline=""))
        return str(destination)
