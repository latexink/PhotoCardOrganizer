from __future__ import annotations

import tomllib
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as project_file:
    VERSION = str(tomllib.load(project_file)["project"]["version"])
OUTPUT = ROOT / "output" / "pdf" / f"PhotoCardOrganizer-{VERSION}-User-Guide.pdf"

NAVY = colors.HexColor("#17233A")
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#667085")
BLUE = colors.HexColor("#1570EF")
CYAN = colors.HexColor("#06AED4")
GREEN = colors.HexColor("#12805C")
PALE_GREEN = colors.HexColor("#EAF8F2")
AMBER = colors.HexColor("#B54708")
PALE_AMBER = colors.HexColor("#FFF4E5")
RED = colors.HexColor("#B42318")
PALE_RED = colors.HexColor("#FDECEC")
PALE_BLUE = colors.HexColor("#EAF2FF")
LINE = colors.HexColor("#D6DCE5")
PAPER = colors.HexColor("#F7F9FC")
WHITE = colors.white


class GuideDocument(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=letter,
            rightMargin=0.62 * inch,
            leftMargin=0.62 * inch,
            topMargin=0.68 * inch,
            bottomMargin=0.58 * inch,
            title=f"Photo Card Organizer {VERSION} User Guide",
            author="The Meat Popsicle; Codex, Latest Robot Overlord",
            subject="Installation and operation guide",
        )
        body = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="body",
        )
        self.addPageTemplates(
            [
                PageTemplate(id="guide", frames=[body], onPage=draw_page),
            ]
        )


def draw_page(canvas, doc) -> None:
    page = canvas.getPageNumber()
    canvas.saveState()
    if page == 1:
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
        canvas.setFillColor(BLUE)
        canvas.rect(0, 0, 0.16 * inch, letter[1], fill=1, stroke=0)
        canvas.setFillColor(CYAN)
        canvas.rect(0.16 * inch, 0, 0.05 * inch, letter[1], fill=1, stroke=0)
    else:
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
        canvas.setStrokeColor(LINE)
        canvas.line(doc.leftMargin, letter[1] - 0.42 * inch, letter[0] - doc.rightMargin, letter[1] - 0.42 * inch)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(doc.leftMargin, letter[1] - 0.3 * inch, "PHOTO CARD ORGANIZER")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(
            letter[0] - doc.rightMargin,
            letter[1] - 0.3 * inch,
            f"USER GUIDE  |  VERSION {VERSION}",
        )
        canvas.line(doc.leftMargin, 0.38 * inch, letter[0] - doc.rightMargin, 0.38 * inch)
        canvas.drawString(doc.leftMargin, 0.22 * inch, "Copy by default. Verify before deletion.")
        canvas.drawRightString(letter[0] - doc.rightMargin, 0.22 * inch, str(page))
    canvas.restoreState()


class Workflow(Flowable):
    def __init__(self, labels: list[str], width: float = 500, height: float = 72):
        super().__init__()
        self.labels = labels
        self.width = width
        self.height = height

    def draw(self) -> None:
        count = len(self.labels)
        gap = 15
        box_width = (self.width - gap * (count - 1)) / count
        y = 18
        for index, label in enumerate(self.labels):
            x = index * (box_width + gap)
            self.canv.setFillColor(NAVY if index == 0 else PALE_BLUE)
            self.canv.setStrokeColor(BLUE)
            self.canv.roundRect(x, y, box_width, 38, 5, fill=1, stroke=1)
            self.canv.setFillColor(WHITE if index == 0 else INK)
            self.canv.setFont("Helvetica-Bold", 8)
            words = label.split()
            lines: list[str] = []
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if stringWidth(candidate, "Helvetica-Bold", 8) > box_width - 12 and current:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                lines.append(current)
            line_y = y + 23 + (len(lines) - 1) * 4
            for line in lines:
                self.canv.drawCentredString(x + box_width / 2, line_y, line)
                line_y -= 10
            if index < count - 1:
                arrow_x = x + box_width
                self.canv.setStrokeColor(CYAN)
                self.canv.setFillColor(CYAN)
                self.canv.line(arrow_x + 2, y + 19, arrow_x + gap - 3, y + 19)
                self.canv.line(arrow_x + gap - 7, y + 23, arrow_x + gap - 3, y + 19)
                self.canv.line(arrow_x + gap - 7, y + 15, arrow_x + gap - 3, y + 19)


class InterfaceMap(Flowable):
    def __init__(self, width: float = 500, height: float = 350):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(NAVY)
        c.roundRect(0, 8, self.width, self.height - 16, 7, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#101827"))
        c.roundRect(8, 17, 130, self.height - 34, 5, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(WHITE)
        c.drawString(18, self.height - 39, "PHOTO CARD ORGANIZER")
        sections = [
            ("WORKSPACE", ["Dashboard", "Libraries", "Cards and drives", "Import or merge", "Digest inboxes"]),
            ("LIBRARY TOOLS", ["Organization", "Travel sync", "Library export", "Integrity", "Conflict review", "Activity"]),
            ("SETTINGS", ["Safety and location", "General options", "Help & about"]),
        ]
        y = self.height - 65
        for section, items in sections:
            c.setFillColor(colors.HexColor("#9FB2CF"))
            c.setFont("Helvetica-Bold", 5.8)
            c.drawString(18, y + 2, section)
            y -= 13
            for item in items:
                if item == "Dashboard":
                    c.setFillColor(colors.HexColor("#24466A"))
                    c.roundRect(14, y - 5, 116, 18, 3, fill=1, stroke=0)
                    c.setFillColor(CYAN)
                    c.rect(14, y - 5, 3, 18, fill=1, stroke=0)
                c.setFillColor(WHITE if item == "Dashboard" else colors.HexColor("#C6D0E1"))
                c.setFont("Helvetica-Bold" if item == "Dashboard" else "Helvetica", 6.6)
                c.drawString(23, y + 1, item)
                y -= 15
            y -= 3
        c.setFillColor(colors.HexColor("#243149"))
        c.roundRect(151, 26, self.width - 163, self.height - 52, 5, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(170, self.height - 52, "Dashboard")
        c.setFillColor(colors.HexColor("#34445F"))
        c.roundRect(170, self.height - 110, 140, 42, 4, fill=1, stroke=0)
        c.roundRect(322, self.height - 110, 140, 42, 4, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#9FB2CF"))
        c.setFont("Helvetica", 7)
        c.drawString(181, self.height - 84, "DESTINATION SPACE")
        c.drawString(333, self.height - 84, "CONNECTED SOURCES")
        c.setFillColor(CYAN)
        c.roundRect(170, self.height - 143, 82, 22, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(211, self.height - 136, "REFRESH CARDS")
        c.setFillColor(colors.HexColor("#34445F"))
        c.roundRect(260, self.height - 143, 83, 22, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.drawCentredString(301.5, self.height - 136, "SCAN NOW")
        c.setFillColor(colors.HexColor("#34445F"))
        c.roundRect(170, 151, 292, 38, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica", 7)
        c.drawString(181, 176, "DESTINATION FOR THIS IMPORT")
        c.drawString(181, 161, "Library                         Folder organization")
        c.setStrokeColor(colors.HexColor("#536580"))
        c.rect(170, 80, 292, 52, fill=0, stroke=1)
        for offset in (13, 26, 39):
            c.line(170, 80 + offset, 462, 80 + offset)
        c.setFillColor(colors.HexColor("#34445F"))
        c.roundRect(170, 42, 96, 22, 4, fill=1, stroke=0)
        c.setFillColor(CYAN)
        c.roundRect(366, 42, 96, 22, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(218, 49, "IMPORT / MERGE")
        c.drawCentredString(414, 49, "IMPORT SELECTED")


def make_styles():
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=34,
            leading=38,
            textColor=WHITE,
            alignment=TA_LEFT,
            spaceAfter=16,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=15,
            leading=22,
            textColor=colors.HexColor("#D8E3F2"),
            spaceAfter=16,
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#8FDBEE"),
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=27,
            textColor=NAVY,
            spaceBefore=2,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            textColor=BLUE,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.3,
            leading=13.6,
            textColor=INK,
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11.5,
            textColor=MUTED,
        ),
        "table": ParagraphStyle(
            "Table",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.1,
            leading=11,
            textColor=INK,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.1,
            leading=10,
            textColor=WHITE,
        ),
        "callout": ParagraphStyle(
            "Callout",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
        ),
        "step_number": ParagraphStyle(
            "StepNumber",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=WHITE,
            alignment=TA_CENTER,
        ),
    }


S = make_styles()


def p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def code(text: str) -> Table:
    block = Preformatted(
        text,
        ParagraphStyle(
            "Code",
            fontName="Courier",
            fontSize=7.6,
            leading=10.5,
            textColor=colors.HexColor("#E7EDF6"),
        ),
    )
    table = Table([[block]], colWidths=[7.05 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#32415A")),
                ("LEFTPADDING", (0, 0), (-1, -1), 11),
                ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def callout(title: str, text: str, kind: str = "info") -> Table:
    palette = {
        "info": (PALE_BLUE, BLUE),
        "safe": (PALE_GREEN, GREEN),
        "warn": (PALE_AMBER, AMBER),
        "danger": (PALE_RED, RED),
    }
    background, accent = palette[kind]
    content = p(f"<b>{title}</b><br/>{text}", "callout")
    table = Table([["", content]], colWidths=[0.08 * inch, 6.97 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BACKGROUND", (0, 0), (0, 0), accent),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 12),
                ("RIGHTPADDING", (1, 0), (1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def steps(items: list[tuple[str, str]]) -> list[KeepTogether]:
    result = []
    for index, (title, body) in enumerate(items, start=1):
        number = Table([[p(str(index), "step_number")]], colWidths=[0.34 * inch], rowHeights=[0.34 * inch])
        number.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), BLUE),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        text = p(f"<b>{title}</b><br/>{body}")
        row = Table([[number, text]], colWidths=[0.48 * inch, 6.57 * inch])
        row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        result.append(KeepTogether([row, Spacer(1, 3)]))
    return result


def data_table(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[p(header, "table_head") for header in headers]]
    data.extend([[p(cell, "table") for cell in row] for row in rows])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for index in range(1, len(data)):
        commands.append(("BACKGROUND", (0, index), (-1, index), WHITE if index % 2 else colors.HexColor("#F1F4F8")))
    table.setStyle(TableStyle(commands))
    return table


def chapter(title: str, intro: str = "") -> list:
    items: list = [p(title, "h1")]
    if intro:
        items.append(p(intro))
    return items


def build_story() -> list:
    story: list = []
    story.extend(
        [
            Spacer(1, 1.0 * inch),
            p("Photo Card<br/>Organizer", "cover_title"),
            p(
                "A practical guide to safe camera-card imports, organized libraries, "
                "verified moves, named destinations, Digest Inboxes, travel sync, "
                "backups, and editing exports.",
                "cover_subtitle",
            ),
            Spacer(1, 0.25 * inch),
            p(f"USER GUIDE  |  VERSION {VERSION}", "cover_meta"),
            Spacer(1, 1.7 * inch),
            Table(
                [[p("WINDOWS + LINUX", "cover_meta"), p("COPY IS THE DEFAULT", "cover_meta"), p("MOVE REQUIRES VERIFICATION", "cover_meta")]],
                colWidths=[2.15 * inch, 2.15 * inch, 2.45 * inch],
                style=TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#4A5D7A")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#4A5D7A")),
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#20304D")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ]
                ),
            ),
            PageBreak(),
        ]
    )

    story += chapter(
        "Start here",
        "Photo Card Organizer monitors identified camera cards and ordinary folders, reads available metadata, and places media into a configurable library. This guide follows the normal setup sequence and highlights every action that can modify source media.",
    )
    story += [Workflow(["Configure", "Review", "Scan", "Copy and verify", "Record"]), Spacer(1, 8)]
    story += [
        callout(
            "The safety promise",
            "Copy leaves source files untouched. Move is performed as copy, cryptographic checksum verification, required backup verification, transfer-log writes, and only then source deletion.",
            "safe",
        ),
        Spacer(1, 12),
        p("Guide map", "h2"),
        data_table(
            ["Section", "Use it for"],
            [
                ["Install and launch", "Install the self-contained package and open the desktop or tray application."],
                ["Manage libraries", "Name local, mounted-network, and removable destinations and select a default."],
                ["Onboard a card", "Identify a card, choose a camera label, configure sorting, and approve the first scan."],
                ["Organize a folder", "Import an existing library or working folder without placing identity files in it."],
                ["Digest incoming folders", "Retain varied folder sources, process only new work, and review per-file state."],
                ["Travel and export", "Move verified sessions between clients and prepare grouped copies for editing."],
                ["Safety and recovery", "Understand checksums, backups, conflicts, space limits, logs, and error handling."],
                ["Reference", "Find folder tokens, command-line operations, and troubleshooting steps."],
            ],
            [1.55 * inch, 5.5 * inch],
        ),
        Spacer(1, 12),
        callout(
            "Before using Move",
            "Run at least one copy session with the real camera, card reader, primary library, and backup drives. Open several imported JPEG, RAW, video, and sidecar files before enabling source deletion.",
            "warn",
        ),
        PageBreak(),
    ]

    story += chapter("1. Install and launch")
    story += [p("Windows", "h2")]
    story += steps(
        [
            ("Run the installer", f"Open <b>PhotoCardOrganizer-Installer-{VERSION}.exe</b>. This is separate from PhotoCardOrganizer.exe, which launches the installed application. The single offline installer contains Python, PySide6, all application dependencies, and this guide."),
            ("Choose integration", "Desktop and Start Menu shortcuts are selected by default for a new installation; login monitoring is optional. Upgrades retain previous choices, so select Desktop shortcut to add one to an existing installation. Launch shortcuts use the application icon and taskbar identity. The Start Menu group includes the PDF guide and Uninstall shortcut. Pinning to the taskbar remains a Windows user action."),
            ("Install, repair, or remove", "The Ready page summarizes the operation before files are changed. When an installation is detected, the same Installer file offers repair/upgrade or uninstall."),
            ("Launch", "Start Photo Card Organizer from the wizard or selected shortcut. Only one application instance runs; a later launch restores the existing window."),
        ]
    )
    story += [code(f"PhotoCardOrganizer-Installer-{VERSION}.exe"), Spacer(1, 10)]
    story += [p("Linux", "h2")]
    story += [
        code(
            f"sudo apt install ./photo-card-organizer_{VERSION}_amd64.deb\n"
            "photo-card-organizer\n\n"
            f"chmod +x PhotoCardOrganizer-{VERSION}-x86_64.AppImage\n"
            f"./PhotoCardOrganizer-{VERSION}-x86_64.AppImage"
        )
    ]
    story += [
        Spacer(1, 8),
        p("The Debian package and AppImage contain the Python application runtime. The same executable also supports command-line operations. Tray availability depends on the desktop environment, particularly on Wayland."),
        callout(
            "Optional ExifTool",
            "When ExifTool is available on PATH, it expands RAW and video metadata support. Files still transfer without it; unavailable fields fall back to other metadata readers, file time, or the retained card profile.",
        ),
        p("Installation maintenance", "h2"),
        p("Keep the downloaded Installer file as the single Windows maintenance entry point. Running it again detects the installed package and offers repair/upgrade or uninstall. General options > Installation and maintenance reports the detected installation and opens the registered uninstaller; Windows Installed apps and the Start Menu expose the same uninstall path. The uninstaller preserves per-user settings, profiles, and logs by default and never targets imported media or transfer records. Developer environments expose their local scripts."),
        PageBreak(),
    ]

    story += chapter("2. Find your way around")
    story += [InterfaceMap(), Spacer(1, 8)]
    story += [
        data_table(
            ["Menu section", "Page", "Purpose"],
            [
                ["Workspace", "Dashboard", "Choose an import destination, select connected cards, then import. Refresh, scan, and pause monitoring are separate controls."],
                ["Workspace", "Libraries", "Set up destinations, import or merge, reorganize existing folders, export media, and maintain metadata."],
                ["Workspace", "Cards and drives", "Guided onboarding, offline profiles, identity folders, and connection status."],
                ["Workspace", "Import or merge", "Follow Source, Destination, and Review steps before adding files from another folder or library."],
                ["Workspace", "Digest inboxes", "Retain incoming folders, run or monitor imports, and review per-file state."],
                ["Library tools", "Organization", "Extensions, folder levels, and filename rules for photos, RAW, videos, sidecars, and records."],
                ["Library tools", "Travel sync", "Direct laptop libraries and publish/catch hubs on SMB, USB, or synchronized folders."],
                ["Library tools", "Library export", "Select captures or groups and make verified editing exports."],
                ["Library tools", "Integrity", "Compare files with saved checksums, establish missing baselines, and open reports."],
                ["Library tools", "Conflict review", "Search, compare files side by side, and bulk-update review status."],
                ["Library tools", "Activity", "Transfer, warning, retry, conflict, and error events."],
                ["Settings", "Safety and location", "Verification, conflicts, space reserves, retries, backups, history, and place names."],
                ["Settings", "General options", "Connected-card check interval, portable settings import/export, and installation maintenance."],
                ["Settings", "Help & about", "PDF manual, changelog, version, and project credits."],
            ],
            [1.1 * inch, 1.35 * inch, 4.6 * inch],
        ),
        Spacer(1, 10),
        p("Progress remains visible below every page. The footer provides Open library, the saved/unsaved status, and Save settings, which is highlighted only when settings change. General options holds the monitoring interval, settings import/export, and installation maintenance. Help & about opens this guide and the changelog."),
        PageBreak(),
    ]

    story += chapter(
        "3. Onboard a card or drive",
        "Onboarding pauses automatic monitoring and collects all settings before writing the root identity or beginning the first scan.",
    )
    story += steps(
        [
            ("Card identity", "Choose the card or drive root, display name, stable card ID, optional camera-name override, and media source folders such as DCIM. An existing identity is detected and loaded."),
            ("Destination", "Choose a named primary library, optional Wedding, Client Shoot, Trip, or custom subfolder route, and transfer method. Copy is recommended for initial use."),
            ("Organization and safety", "Choose a folder preset or retain detailed rules, enable media classes, select copy verification and move checksum, set destination reserves, and optionally enable online place names."),
            ("Review", "Read the concise source, destination, operation, media, camera, verification, organization, and backup summary. Nothing is written or scanned until the <b>Confirm initial scan and import</b> prompt is accepted."),
        ]
    )
    story += [
        Spacer(1, 7),
        callout(
            "Move receives another warning",
            "After the initial review, Move displays a separate confirmation naming the checksum algorithm, required logs, and required backup destinations before transfer work begins.",
            "warn",
        ),
        p("Identity layout", "h2"),
        code("CARD_ROOT/\n|-- .photocard/\n|   |-- identity.json\n|   `-- transfers/\n`-- DCIM/"),
        p("The identity folder is stored at the card root, not inside DCIM. Its folder name, identity filename, history folder, and transfer-record naming are configurable. The root marker lets multiple computers recognize the same card."),
        p("Retained profiles", "h2"),
        p("A matching local profile retains the card name, root, camera override, source folders, library subfolder, operation, enabled state, and empty-folder cleanup preference. Offline profiles remain editable while the card is disconnected."),
        PageBreak(),
    ]

    story += chapter("4. Camera metadata and folder organization")
    story += [
        p("Named library destinations", "h2"),
        p("Libraries retains multiple primary destinations and one default. Set up library creates or connects a destination; connecting an existing folder does not scan, import, or move media. A destination may be local, removable, or a network folder already mounted and authenticated by the operating system. Each library keeps versioned identity, migration state, manifests, and session records under its own .photocard-organizer folder. Initialize, check, upgrade, or repair metadata requires a separate confirmation and never changes media files."),
        p("Camera make and model are extracted automatically from EXIF when available. ExifTool is preferred when installed; Pillow and ExifRead provide additional fallbacks. The Camera folder token resolves in this order:"),
        Workflow(["Card camera override", "EXIF model", "EXIF make", "Card display name"]),
        callout(
            "When to use the override",
            "Leave Camera name override empty for normal EXIF behavior. Enter a value when a camera writes inconsistent model strings, metadata is absent, or several cards should share one controlled library label.",
        ),
        callout(
            "Camera folders reduce collisions, but do not guarantee uniqueness",
            "Two bodies of the same model normally report the same EXIF camera label, and many cameras restart names such as IMG_0001. For a mixed library, keep the override blank, organize by date and camera, and use a filename such as {date:%Y%m%d_%H%M%S}_{original}. Any remaining different-content collision is preserved in the local conflict-review folder.",
            "warn",
        ),
        p("Folder presets", "h2"),
        data_table(
            ["Preset", "Result below the optional library subfolder"],
            [
                ["Date then camera", "Media type / year / shoot date / camera"],
                ["Camera then date", "Media type / camera / year / shoot date"],
                ["Date only", "Media type / year / shoot date"],
                ["Media folder only", "Media type"],
                ["None (no folders)", "Filename directly below the library subfolder"],
                ["Use current detailed rules", "Keeps the independent Organization-page levels for each media class"],
            ],
            [1.75 * inch, 5.3 * inch],
        ),
        p("Detailed folder tokens", "h2"),
        data_table(
            ["Token", "Example", "Source"],
            [
                ["{date:%Y-%m-%d}", "2026-07-12", "Capture date, then file time fallback"],
                ["{date:%m - %B}", "07 - July", "Standalone capture month"],
                ["{date:%d}", "12", "Standalone capture day"],
                ["{date:%G-W%V}", "2026-W28", "ISO Monday-start week; week-year can differ near New Year"],
                ["{camera}", "Canon EOS R5", "Override or EXIF camera"],
                ["{make} / {model}", "Canon / EOS R5", "EXIF make or model"],
                ["{location}", "Asheville, North Carolina", "GPS and optional online place lookup"],
                ["{rating}", "4 stars", "EXIF/XMP rating"],
                ["{card}", "R5 Card A", "Retained card name"],
                ["{media}", "Raw", "Detected media class"],
                ["{capture_group}", "Long Exposure Brackets", "High-confidence conditional group; omitted otherwise"],
                ["{original}", "IMG_0421.CR3", "Original filename"],
            ],
            [1.55 * inch, 1.75 * inch, 3.75 * inch],
        ),
        p("Conditional long-exposure folders", "h2"),
        p("Capture grouping can detect nearby photo/RAW exposures that share a source folder and camera label, contain at least the configured number of captures, include a long exposure, and vary shutter duration or exposure bias. Add Long-exposure brackets as a media folder level to use the result. Non-qualifying files omit that level; same-stem sidecars inherit a qualifying capture's group."),
        PageBreak(),
    ]

    story += chapter("5. Import or merge a library")
    story += [
        p("Use <b>Import or merge</b> for media already stored in a normal folder, another managed library, a backup, or a removable transfer drive. This workflow never creates a .photocard identity or portable history inside the source."),
    ]
    story += steps(
        [
            ("Source", "Select the source folder, accept or edit its source name, and choose whether the scan includes subfolders. Folder selection does not open another dialog or begin analysis."),
            ("Destination", "Choose the receiving library, optional import grouping, Copy or verified Move, enabled media types, folder layout, and optional camera override. Event/project fields appear only for a named grouping. Analyze source folders only when a read-only editable mapping would be useful."),
            ("Review", "Read the exact scan scope, destination, operation, media, organization, verification, and backup plan. Save settings + Import opens the final confirmation, saves the reviewed settings, and only then starts the scan; Move then receives the separate checksum-and-deletion warning."),
        ]
    )
    story += [
        callout(
            "Detected mappings follow the source scope",
            "Changing the source path, recursive scan setting, or enabled media types clears a previously detected mapping. Analyze again or choose another folder layout so stale source assumptions are never reused silently.",
        ),
        callout(
            "Analysis preview versus full import",
            "Structure analysis previews at most 10,000 filesystem files and offers up to twelve editable source-folder levels. If the preview limit is reached, the dialog says so and shows how many files informed the mapping. The eventual import is not capped and scans every enabled media file in the selected scope.",
            "safe",
        ),
        callout(
            "Why a separate destination is required",
            "Import or merge requires separate source and destination folders. To change folders inside one managed library, use Libraries > Reorganize library, which freezes the input list and previews moves first.",
            "warn",
        ),
        p("Supported file classes", "h2"),
        p("JPEG/photos, RAW, video, and sidecar files each have an enabled switch, customizable extension list, one to twelve independent folder levels, and a filename template. Add or remove levels in Organization, enter a fixed folder name directly, disable an unneeded class, or set a level to None where no directory segment is desired."),
        p("First backup-library test", "h2"),
        p("Use an empty destination, Copy, SHA-256 copy verification, and recursive scanning. Keep Camera override empty so each file can use its own EXIF make/model. Different-content filename collisions are preserved automatically in the local conflict-review folder. Exact-content duplicates follow the separately configured duplicate policy; a second run of the same retained source should report no new imports. The current manifest tracks stable sources and expected destination collisions, but is not yet a whole-library fingerprint catalog for identical media arriving through unrelated historical paths."),
        PageBreak(),
    ]

    story += chapter("Merge and migrate libraries")
    story += [
        p("Merge library", "h2"),
        p("In Libraries, select the receiving library and choose <b>Merge library</b>. Select one source folder or managed library, save any pending settings, then choose Preview changes. The preview hashes enabled media across both locations, uses saved organization rules for incoming files, and shows full source/destination paths. Existing destination media keeps its layout."),
        p("Identical content within a media class has one destination even when filenames differ. Different-content name collisions go to the configured Conflicts folder with the organized hierarchy retained. Source media stays in place. Sidecars are compared separately; distinct editing metadata is preserved. Repeat the workflow for additional sources."),
        p("Confirm and process copies unique files, verifies SHA-256, and saves portable integrity baselines plus local session records. Baseline mismatches are reported without replacing the old checksum. Cancel waits for the current file before releasing the library back to monitoring."),
        p("Migrate library", "h2"),
        p("Select the library and choose <b>Migrate library</b>, then choose an empty destination folder. Close other clients using either location. Preview includes media, library identity, transfer history and index files. After verified copying, the destination index paths are updated and the saved library location changes. The default library selection is preserved."),
        callout("Originals remain available", "Migration retains the original library. Inspect the destination before any manual cleanup. On an interrupted migration, the original location remains active. Retry within the open dialog after resolving the error; after restarting, select a new empty destination and retain the partial copy until reviewed.", "warn"),
        p("Scope of this preview", "h2"),
        p("Merge and migration are dedicated Libraries actions. They do not yet replace the older Import or merge wizard. Multi-source consolidation with a clone selector, media-routing controls for general imports, automatic repair, and scheduled integrity checks remain planned. Integrity catalogs detect changes; recovery requires an independent good copy."),
        PageBreak(),
    ]

    story += chapter("Reorganize an existing library")
    story += steps([
        ("Choose the library", "In Libraries, select an available library and choose Reorganize library. Save any pending settings first. Monitoring pauses while the plan is prepared."),
        ("Choose the layout", "Separate by media type creates Photos, RAW, Videos, and Sidecars folders. Other presets add date or camera levels; Use current detailed rules keeps the saved folder definitions, including conditional bracket folders. Choose which media classes to include. Filenames are retained."),
        ("Preview changes (optional)", "Read current and proposed paths, including Already organized and potential Conflict review entries. Preview does not change media or save settings. You can select Reorganize directly to calculate the plan and continue to the final confirmation."),
        ("Review and reorganize", "Read the explicit move warning and summary before accepting. Use this folder layout for future imports commits only the selected folder rules when processing starts; these rules apply to future imports across libraries and do not change the default library. Remove folders left empty is optional."),
    ])
    story += [
        callout("Preserve an independent backup", "Same-filesystem changes use durable no-overwrite renames without reading media content again. Cross-filesystem moves use a verified destination copy before deleting the original. Required backups and local transfer records must succeed. File changes since planning are deferred; failed catalog updates retain a resumable intent. Existing filenames are never overwritten, including exact duplicates: conflicts are kept in the configured organized conflict folder for review.", "warn"),
        p("The fixed plan prevents generated files from becoming new inputs. Metadata, identity, and conflict folders are excluded. Local catalog paths follow moved files; historical session logs remain append-only. Compatible folders can move as one rename only when every member is known media; an unrelated file keeps the operation at the individual-file level. Optional checksum creation reads only files with no baseline. On failure, review Activity and retry; do not manually remove a pending rename intent."),
        p("Location rules use place names already cached locally during preview; no online requests are made. Missing capture dates use file modification time. This action changes folders only: edit filename templates for future imports in Organization or use Import or merge into a separate library for a reviewed rename workflow."),
        PageBreak(),
    ]

    story += chapter("Integrity checks")
    story += [
        p("Open Integrity or choose Verify integrity from Libraries. Select a library and optionally select individual files, then use Verify library. Each checked file is read once and compared with its saved checksum. Results distinguish Verified, Changed; baseline retained, Missing, and No baseline. A report is saved in the library and matching local application state."),
        p("Create missing checksums records the current contents only for files without a baseline. It never replaces an existing checksum and cannot prove that a new baseline is an original good copy. Cancel stops after the current read chunk and retains completed results in the report."),
        callout("Baseline records", "The portable catalog is stored at .photocard-organizer/integrity/catalog.sqlite3. Existing root-level integrity.sqlite3 catalogs are copied atomically into this location when first written and the older file remains as a rollback copy. Do not compress the live SQLite catalog or journal files.", "info"),
        PageBreak(),
    ]

    story += chapter("6. Digest incoming folders")
    story += [
        p("<b>Digest inboxes</b> retains ordinary incoming folders with any existing hierarchy. A source may be a local staging folder, removable drive, SMB/UNC path, Linux mount, or locally synchronized cloud folder. It must remain separate from the managed master library."),
    ]
    story += steps(
        [
            ("Add an inbox", "Choose a stable name and incoming folder. Enable subfolders for a varied legacy tree, then optionally select a master-library subfolder or camera override."),
            ("Choose source handling", "Leave Copy selected for normal use. Optional automatic checks are copy-only and use the configured interval. Move is manual and disables automatic digestion."),
            ("Review and digest", "Select one or more available inboxes. Confirm source names, master destination, copy and move verification, required backups, organization rules, and any offline sources before scanning."),
            ("Review state", "Filter the queue by pending, processed, failed, or conflict. The table labels the latest visible entries against the complete retained total; each relative source path keeps its state in the local manifest, and each manual scan creates a separate digest run record."),
        ]
    )
    story += [
        callout(
            "Copy does not multiply unchanged files",
            "Copy leaves the incoming source in place, but a later scan recognizes the same relative path, size, and modification time and reports it as already handled instead of creating another destination copy. A changed file is treated as new work.",
            "safe",
        ),
        callout(
            "Verified move is a deliberate cleanup action",
            "Move requires the normal review and a second warning. A source is removed only after its primary destination, every required backup, cryptographic checksum, and required transfer records succeed. Any failure leaves that source in place for retry.",
            "warn",
        ),
        p("Cloud and shared folders", "h2"),
        p("Photo Card Organizer works with the local folder exposed by the operating system or synchronization client. Automatic digestion never deletes source files. Avoid Move on a synchronized folder unless remote deletion propagation is intentional, separately backed up, and tested with disposable data."),
        p("Forgetting a profile removes only its saved connection. Incoming files, master-library files, and retained local digest history are unchanged."),
        PageBreak(),
    ]

    story += chapter("7. Copy and verified Move")
    story += [p("Copy", "h2"), Workflow(["Discover", "Write temporary copy", "Verify", "Place atomically", "Write records"])]
    story += [
        p("Copy is the default globally and per card. Existing destination files are not overwritten. The source remains unchanged even if a backup, log, location lookup, or metadata field fails."),
        p("Move", "h2"),
        Workflow(["Copy", "Checksum", "Verify required backups", "Write required logs", "Delete source"]),
        callout(
            "Deletion gate",
            "The source is retained whenever checksum verification, required replica verification, portable history, or required local history is incomplete. A verified primary copy may remain for safe retry without being duplicated.",
            "safe",
        ),
        p("If the computer loses power during a temporary copy, the source remains. On the next write to that folder, the app removes abandoned partial files owned by the same client; it does not remove another client's in-progress partials."),
        p("Verification choices", "h2"),
        data_table(
            ["Setting", "Choices", "Notes"],
            [
                ["Copy verification", "Size, SHA-256, SHA-512, BLAKE2b", "Size is faster; cryptographic choices compare content."],
                ["Move checksum", "SHA-256, SHA-512, BLAKE2b", "Cryptographic verification is mandatory for source deletion."],
                ["Replica verification", "SHA-256, SHA-512, BLAKE2b", "Required replicas must verify before completion."],
            ],
            [1.45 * inch, 2.1 * inch, 3.5 * inch],
        ),
        p("The footer progress bar reports files checked during scans and imports. Activity records meaningful events without inserting every progress tick."),
        PageBreak(),
    ]

    story += chapter("8. Backups, clones, and free space")
    story += [
        p("Add multiple destinations under Safety and location > Backups and clones. Each destination can be enabled, required or optional, configured to receive matching history, and assigned a replica conflict policy."),
        data_table(
            ["Destination type", "Import behavior", "Move behavior"],
            [
                ["Primary library", "Always required", "Must verify before deletion"],
                ["Required backup", "Failure leaves work pending for retry", "Must verify before deletion"],
                ["Optional clone", "Failure is recorded as a warning", "Does not block deletion after all required targets succeed"],
            ],
            [1.55 * inch, 2.75 * inch, 2.75 * inch],
        ),
        p("Space safeguards", "h2"),
        p("The application checks both minimum free percentage and minimum free GB. It can block, try fallback destinations then block, or continue below the configured reserve when enough physical bytes remain."),
        callout(
            "A full drive never offers Continue",
            "Manual space prompts can offer another destination, skip, or stop. Continue below reserve appears only when the drive has enough physical space for the file.",
            "danger",
        ),
        p("A low source free-space threshold is informational. It never silently changes Copy to Move."),
        PageBreak(),
    ]

    story += chapter("9. Duplicates and filename conflicts")
    story += [
        p("Before placement, same-name files are compared by content. A different-content collision never pauses the active task: the existing file stays in place, the incoming file is copied below the local conflict-review folder, and both are queued for review. Exact-content duplicates retain a separate configurable policy."),
        data_table(
            ["Policy", "Result"],
            [
                ["Different content", "Always place the incoming file below the configured local conflict-review root while retaining the normal media/date/camera hierarchy."],
                ["Exact duplicate: rename", "Keep the organized destination and append a configurable value such as _2, (2), or a custom {number} pattern."],
                ["Exact duplicate: conflict folder", "Preserve the second copy below the configured conflict-review root."],
                ["Exact duplicate: skip", "Leave the source and existing destination unchanged and record no new imported copy."],
            ],
            [1.45 * inch, 5.6 * inch],
        ),
        p("Conflict review", "h2"),
        p("Preserved conflicts are queued in Conflict review. SQL-backed search and fixed 200-record pages keep very large histories bounded. Select one row for side-by-side paths, sizes, modification times, and supported image previews. Multi-select routine groups to mark their review status together; this status change never modifies either media file. RAW or video formats without a preview still show details and can be opened externally."),
        callout(
            "Filename conflicts do not interrupt",
            "Different-content filename collisions are always preserved for later review. Other configured safety conditions, including exact duplicates, low space, and file errors, may still request a decision during a manual import.",
        ),
        PageBreak(),
    ]

    story += chapter("10. Transfer records and multiple computers")
    story += [
        p("Each transfer session receives its own JSON Lines record and, when cryptographic verification is used, a separate checksum file. Session names can include date, card, computer, session, instance, library, card ID, and algorithm tokens. Library metadata and application-owned records use the canonical .photocard-organizer folder."),
        code("CARD_ROOT/.photocard/transfers/2026/2026-07/\n  2026-07-12_14-30-05_R5-Card_Studio-PC_a1b2c3d4.jsonl\n  2026-07-12_14-30-05_R5-Card_Studio-PC_a1b2c3d4.sha256"),
        p("Matching local records", "h2"),
        code("DESTINATION/.photocard-organizer/transfer-records/CARD_ID/"),
        p("The destination also stores a local SQLite manifest for already-imported source lookup, pending-transfer recovery, Digest Inbox item/run state, same-name conflict review, hub receipts, and cached place names. It is not a whole-library content-deduplication engine."),
        p("Cross-computer use", "h2"),
        p("The card identity and portable session records allow another configured computer to recognize the same card and determine which source items were already handled. Retained client profiles control local preferences while the stable card ID links the records."),
        callout(
            "Do not manually edit active session files",
            "A malformed or unwritable required record blocks source deletion. Preserve identity and history folders when reformatting or retiring a card if the audit history is still needed.",
            "warn",
        ),
        p("Identity and session records remain uncompressed by default. They are generally tiny beside camera media, directly readable during recovery, and safer to synchronize between computers as separate completed files."),
        PageBreak(),
    ]

    story += chapter("11. Travel sync and shared transfer hubs")
    story += [
        p("<b>Travel sync</b> provides two copy-only ways to reconcile a laptop or removable library with the main desktop library. Use the Laptop libraries tab for a directly reachable source and Shared hubs for USB, SMB/NAS, or locally synchronized cloud-folder transport."),
        data_table(
            ["Method", "Best use", "Behavior"],
            [
                ["Retained travel source", "A laptop share, attached travel drive, or USB library", "Scan that source directly and retain its stable profile so later runs import only new work."],
                ["Publish/catch hub", "USB shuttle drive, SMB/NAS share, or locally synchronized cloud folder", "A producer publishes verified sessions to its channel; a consumer catches new sessions and records digestion receipts."],
            ],
            [1.55 * inch, 2.35 * inch, 3.65 * inch],
        ),
        p("USB drive workflow", "h2"),
    ]
    story += steps(
        [
            ("Configure the laptop", "Add a hub on the USB drive with role Publish and a unique producer channel, such as Field-Laptop. A card import can copy to this hub as a replica, or Publish now can backfill media imported while the drive was absent."),
            ("Wait for completion", "Confirm the publish progress and session record finish before using the operating system's safe-eject command. Source and laptop-library files are never deleted by hub publication."),
            ("Configure the desktop", "Attach the USB drive, add the same hub folder with role Catch, and select Catch new sessions. The desktop applies its own organization rules to the incoming producer channel."),
            ("Confirm digestion", "After the desktop manifest records the import, the app writes matching local and shared receipts. A repeated catch should import zero new files."),
        ]
    )
    story += [
        callout(
            "Paths can change",
            "If Windows assigns a different USB drive letter, or Linux mounts the drive at a different path, edit the saved hub folder before running it. The producer channel and recorded sessions remain unchanged.",
            "warn",
        ),
        p("Network and cloud folders", "h2"),
        p(f"For SMB/NAS, authentication and reconnect behavior are handled by the operating system. For Google Drive or another cloud service, choose a normal locally synchronized folder and make its files available offline. Version {VERSION} does not request cloud API credentials, and reported local free space may not reflect remote quota."),
        p("Use one role per client for a given hub and a distinct producer channel for every publishing laptop or tablet. This prevents circular publication and keeps receipts attributable."),
        PageBreak(),
    ]

    story += chapter("12. Export captures for editing")
    story += [
        p("Choose a library in <b>Libraries</b>, then <b>Export media</b>, or open <b>Library export</b> and select a saved library. This does not change the default import destination. Scanning reads metadata without changing source files. Matching stems and corresponding organized paths become capture sets; Photos, RAW, and Sidecars partitions are normalized during matching."),
    ]
    story += steps(
        [
            ("Scan the selected library", "Choose a saved library and select Scan library. Internal manifest and transfer-record folders are excluded. Changing libraries clears the old selection."),
            ("Filter media and dates", "Choose All media, Photos, RAW, Videos, or Sidecars. Enable Capture date range for inclusive start/end dates; missing capture metadata falls back to file modification time. Include matching sidecars is optional. For example, select Videos and the required dates, then Select all matching."),
            ("Review detected sets", "Adjust the bracket/burst and interval thresholds, then review camera, capture time, rating, media types, and group labels."),
            ("Select work", "Select one or more captures, Select all matching, or expand a detected group. Group expansion stays inside the active media/date filters. Optional group subfolders keep bracketed and interval sequences together."),
            ("Export", "Choose a separate editing folder and review the confirmation, including group folders, conflict suffix, and free-space reserve. Export is always copy-only, uses SHA-256 verification, reuses exact existing files, preserves different-content filename collisions with a suffix, and writes a JSON Lines record under .photocard-organizer/export-sessions."),
        ]
    )
    story += [
        callout(
            "Grouping is a review aid",
            "Bracket and interval detection is based on timestamps, folder, and compatible camera labels. Review the selected rows before export; the app does not infer creative intent or alter the master library.",
        ),
        p("The editing folder cannot be inside the master library or contain it. The configured destination free-percent and free-GB reserves also apply to editing exports. If a source changes while it is being copied, the issue is recorded and no partial destination is committed."),
        PageBreak(),
    ]

    story += chapter("13. Tray, monitoring, and settings portability")
    story += [
        p("Closing the window hides the application when tray support is available. The tray menu can restore the window, scan now, pause or resume monitoring, and quit. Copy cards and enabled copy-only Digest Inboxes can be handled automatically; Move sources wait for an explicit manual confirmation."),
        p("New settings check connections every 30 seconds. Unchanged card file scans gradually back off to the Maximum idle-card scan interval, initially 300 seconds, to reduce disk activity. Scan now forces discovery and scanning immediately. New mount changes trigger discovery without forcing unchanged connected cards to rescan. A card replaced at the same drive letter between checks, or changes on a still-mounted source, may wait until the next idle scan. Saved intervals are retained; Digest Inbox and hub intervals remain separate."),
        p("Portable client settings", "h2"),
        p("Export settings creates a JSON file containing organization, named-library definitions, safety, media, card-profile, backup, travel/hub, Digest Inbox, and history definitions. Machine-specific library, card, backup, travel, hub, digest, fallback, and local-log paths are removed. Named libraries, travel sources, hubs, and Digest Inboxes arrive without another computer's local path; importing over the same stable ID retains that client's existing path and enabled state."),
    ]
    story += steps(
        [
            ("Export", "Open General options > Export settings and store the JSON file securely."),
            ("Install on the other computer", "Complete installation and choose local destination and log paths."),
            ("Import", "Use Import settings, review the merge confirmation, and then map retained card or backup roots as needed."),
        ]
    )
    story += [
        p("Online place names", "h2"),
        p("Online GPS-to-place lookup is off by default. When enabled, coordinates are sent to the configured provider. Nominatim requests are rate-limited and cached locally. Lookup failure never blocks transfer; the folder rule falls back to coordinates or Unknown location."),
        PageBreak(),
    ]

    story += chapter("14. Troubleshooting")
    story += [
        data_table(
            ["Symptom", "What to check"],
            [
                ["Windows shows an unknown publisher", "The private release is currently unsigned. Verify the installer filename and SHA-256 checksum before choosing Run anyway."],
                ["Installer reports the app is running", "Close the main window and quit from the tray icon, then continue. The installer uses the application mutex to prevent files from changing underneath a running transfer."],
                ["Application opens instead of installer", "PhotoCardOrganizer.exe is the installed application launcher. Run the separately downloaded PhotoCardOrganizer-Installer-version.exe for install, repair, upgrade, or maintenance uninstall."],
                ["Structure preview reaches its limit", "The 10,000-file limit applies only to organization analysis. Review the proposed mapping; the confirmed import still scans the complete selected source scope."],
                ["Installation verification failed", "Run the same Installer file again to repair the package. Existing settings and card profiles are stored separately and remain in place."],
                ["Card is not detected", "Confirm the card root contains the configured identity folder and identity filename; refresh cards; check the retained root; reconnect the reader."],
                ["No files were imported", "Check enabled media classes and extensions, source folders, recursion, settle time, transfer history, and Activity messages."],
                ["Move kept the source", "This is a safety result. Review checksum, required backup, portable history, local history, destination space, and permission errors."],
                ["Destination is unavailable", "Reconnect the drive, choose an alternate destination during a manual import, or configure fallback roots."],
                ["USB hub is offline", "Reconnect it and confirm the saved hub folder. If the drive letter or mount point changed, edit the profile before Publish or Catch."],
                ["Hub catch repeats files", "Confirm both clients use stable producer/consumer identities and do not delete the hub session records or local manifest. Review receipt status."],
                ["Digest Inbox repeats a file", "Confirm the profile ID and local manifest were retained and the source path, size, or modification time did not change. A changed source is intentionally new work."],
                ["Digest move kept the source", "Review the per-file failed state, Activity, checksum, required backup, required record, source-change, and destination-space results. Keeping the source is the safe outcome."],
                ["A conflict was preserved", "Open Conflict review. Search by filename, compare both files, and use their external-open actions. Multi-select only records you have actually reviewed."],
                ["The in-app manual is unavailable", "Repair the installed package or rebuild the release so the PDF version matches the application version exactly."],
                ["Tray icon is absent on Linux", "Verify StatusNotifierItem/AppIndicator support. GNOME may need an AppIndicator extension. The window remains usable without tray support."],
                ["Scrolling appears inactive", "Place the pointer over the form area. Lists, tables, and text regions intentionally handle their own wheel events."],
                ["An import action asks to save first", "Save settings is highlighted because visible controls differ from the saved configuration. Save before normal processing, or use the reviewed Import or merge action and its Save settings + Import button."],
            ],
            [2.0 * inch, 5.05 * inch],
        ),
        Spacer(1, 10),
        callout(
            "Safe response to an unfamiliar error",
            "Choose Skip or Stop rather than forcing continuation. Source media is retained when transfer integrity is uncertain. Review Activity and the session records before retrying.",
            "safe",
        ),
    ]

    story += chapter("15. Command reference")
    story += [
        code(
            "python app.py                         Open the desktop application\n"
            "python app.py --service               Start hidden in the tray\n"
            "python app.py --scan-once --dry-run   Preview connected-card work\n"
            "python app.py --scan-once             Import copy cards once\n"
            "python app.py --scan-once --confirm-move\n"
            "python app.py --import-folder D:\\Incoming --import-name \"Travel\"\n"
            "python app.py --import-folder /mnt/incoming --no-subfolders\n"
            "python app.py --digest-inbox legacy-drop\n"
            "python app.py --digest-all\n"
            "python app.py --digest-inbox disposable-move --confirm-move\n"
            "python app.py --export-settings client-settings.json\n"
            "python app.py --import-settings client-settings.json\n"
            "python app.py --install-autostart\n"
            "python app.py --init-config\n"
            "python app.py --version"
        ),
        p("For a Debian installation, replace <b>python app.py</b> with <b>photo-card-organizer</b>. For an AppImage, use its path. All command-line arguments are forwarded to the bundled executable."),
        p("Create a card identity from the command line", "h2"),
        code("python app.py --create-identity E:\\ --card-name \"Canon R5 Card A\" --card-id canon-r5-a"),
        p("Use <b>--digest-inbox PROFILE_ID</b> more than once to run selected saved profiles, or <b>--digest-all</b> for every enabled profile. Move operations from the command line require the explicit <b>--confirm-move</b> flag. Without it, destructive work remains blocked."),
        p("Validate the release", "h2"),
        code("python -m unittest discover -s tests -v"),
        p(f"Version {VERSION} includes automated checks for named libraries, versioned metadata migration, organization, bracket confidence and I/O, folder recursion, retained profiles, verified moves, Digest Inbox repeat safety and background polling, local and portable records, replicas and transfer hubs, travel-source reconciliation, grouped editing exports, retries, automatic conflict routing, large conflict paging/search/bulk review, portable settings, packaging/icon contracts, UI workflows, space refusal, source mutation, destination races, timestamps, and single-instance activation."),
        PageBreak(),
    ]

    story += chapter(
        "Release history",
        "The complete cumulative CHANGELOG.md is included with every packaged release and opens from Help & about.",
    )
    story += [
        data_table(
            ["Version", "Released", "Highlights"],
            [
                ["0.10.0", "2026-09-08", "Integrity checks, resumable same-filesystem reorganization, lower-I/O verification, and expanded folder levels."],
                ["0.9.0", "2026-09-07", "Content-based library merge, verified location migration, portable integrity baselines, and dialog lifecycle fixes."],
                ["0.8.0", "2026-09-06", "In-place library reorganization, filtered named-library exports, and reduced scanning overhead."],
                ["0.7.2", "2026-09-06", "Fixed Windows Qt packaging and added full installed-GUI verification."],
                ["0.7.1", "2026-07-31", "Simplified Libraries and Import or merge workflows, clearer metadata actions, destination carry-over, and focused progressive disclosure."],
                ["0.7.0", "2026-07-28", "Named libraries, schema-5 and library metadata migration, named import routes, Month and conditional long-exposure folders, saved-settings enforcement, and persistent progress."],
                ["0.6.1", "2026-07-27", "Large/deep library analysis clarity, twelve editable levels, bounded-result transparency, and clearly named Windows maintenance."],
                ["0.6.0", "2026-07-27", "Guided existing-library import, explicit structure analysis, focused Travel tabs, in-app changelog, and clearer disabled actions."],
                ["0.5.0", "2026-07-26", "Digest Inboxes, scalable conflict review, Help/manual/changelog access, and high-resolution taskbar/tray icons."],
                ["0.4.0", "2026-07-26", "Travel libraries, shared USB/SMB/cloud-folder hubs, digestion receipts, grouped editing exports, and consolidated maintenance."],
                ["0.3.0", "2026-07-18", "Upgradeable Windows installer, repair/uninstall flow, Linux packages and CLI, schema migration, and versioned PDF packaging."],
                ["0.2.0", "2026-07-15", "PySide6 desktop/tray UI, multi-card queue, existing-library import, profiles, replicas, conflicts, and workflow polish."],
                ["0.1.0", "2026-07-15", "First working camera-card import release preserved as a separate snapshot."],
            ],
            [0.8 * inch, 1.05 * inch, 5.2 * inch],
        ),
        PageBreak(),
    ]

    story += chapter("Quick operating checklist")
    story += [
        data_table(
            ["Before import", "After import"],
            [
                ["Destination and required backups are connected", "Progress completed and Activity has no unresolved error"],
                ["Correct card or folder source is selected", "Representative JPEG, RAW, video, and sidecar files open"],
                ["Copy is selected for a new workflow", "Folder names match date, camera, location, and rating expectations"],
                ["Enabled extensions match the camera", "Required backup copies and transfer records exist"],
                ["Digest Inbox is separate and Copy is selected", "Per-file queue is processed and a repeat imports nothing"],
                ["USB/network hub path and role are correct", "Publish/catch receipts exist and a repeated catch imports nothing"],
                ["Free-space reserves leave adequate headroom", "Only then consider enabling Move for later sessions"],
            ],
            [3.52 * inch, 3.53 * inch],
        ),
        Spacer(1, 16),
        callout(
            "Recommended first session",
            "Onboard one card, leave Copy selected, use Date then camera, enable only the media classes the camera writes, and import a small test shoot. Inspect primary and backup results before unattended use.",
            "info",
        ),
        Spacer(1, 20),
        p(f"Release: Photo Card Organizer {VERSION}", "h2"),
        p(f"This guide applies to Photo Card Organizer {VERSION}. Configuration and transfer-record files are plain JSON or JSON Lines; the local manifest is SQLite. Imported media is never intentionally overwritten."),
        Spacer(1, 10),
        p("Programmers / designers", "h2"),
        p("<b>The Meat Popsicle</b> - product direction, design judgment, field testing, and possession of the cameras.<br/><b>Codex, Latest Robot Overlord</b> - programming, systems design, documentation, and strictly limited authority over folder names."),
    ]
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = GuideDocument(str(OUTPUT))
    document.build(build_story())
    print(OUTPUT)


if __name__ == "__main__":
    main()
