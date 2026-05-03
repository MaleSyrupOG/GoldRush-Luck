"""
Generates the DeathRoll Luck v1 Economics Proposal PDF for collaborator review.

NOTE (2026-04-29): this PDF is HISTORIC. It was sent to the collaborator and
their answers have been incorporated into the locked design at:
  docs/superpowers/specs/2026-04-29-deathroll-luck-v1-design.md (§10)
The locked design supersedes everything in this document. Flower Poker was
removed from scope; house edges were unified at 5%; raffle threshold and
rake were finalised. The script and its output are kept for traceability
of how the design evolved.

Visual style follows the DeathRoll design system: deep ink background, gold/ember
accents, parchment-tone body text. Built with reportlab Platypus.
"""

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# ---------- DeathRoll palette (from deathroll-design-system.html) ----------
INK_1000 = colors.HexColor("#06060B")
INK_900 = colors.HexColor("#11101A")
INK_800 = colors.HexColor("#191623")
INK_700 = colors.HexColor("#221C2A")
INK_600 = colors.HexColor("#2D2533")
GOLD_500 = colors.HexColor("#F2B22A")
GOLD_400 = colors.HexColor("#FFC83D")
GOLD_300 = colors.HexColor("#FFD96B")
GOLD_700 = colors.HexColor("#B07816")
GOLD_900 = colors.HexColor("#7A4A0E")
EMBER_500 = colors.HexColor("#C8511C")
EMBER_700 = colors.HexColor("#7A2E10")
BONE = colors.HexColor("#F4E8C9")
PARCHMENT = colors.HexColor("#E5D7B0")
SILT = colors.HexColor("#A89B7E")
WIN = colors.HexColor("#5DBE5A")
BUST = colors.HexColor("#D8231A")


OUTPUT = Path(__file__).parent / "DeathRoll_Luck_v1_Economics_Proposal.pdf"


# ---------- Page background ----------
def draw_background(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(INK_1000)
    canvas.rect(0, 0, width, height, stroke=0, fill=1)

    # Top accent bar
    canvas.setFillColor(GOLD_500)
    canvas.rect(0, height - 4 * mm, width, 4 * mm, stroke=0, fill=1)

    # Bottom accent bar
    canvas.setFillColor(EMBER_500)
    canvas.rect(0, 0, width, 2 * mm, stroke=0, fill=1)

    # Footer
    canvas.setFillColor(SILT)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(
        2 * cm,
        1 * cm,
        "DeathRoll Luck — Economics Proposal v1 — Confidential",
    )
    canvas.drawRightString(
        A4[0] - 2 * cm,
        1 * cm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


# ---------- Styles ----------
styles = getSampleStyleSheet()

style_h1 = ParagraphStyle(
    "h1",
    parent=styles["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=28,
    leading=32,
    textColor=GOLD_400,
    alignment=TA_LEFT,
    spaceAfter=4,
    spaceBefore=0,
)
style_h1_sub = ParagraphStyle(
    "h1_sub",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=11,
    leading=14,
    textColor=SILT,
    alignment=TA_LEFT,
    spaceAfter=18,
)
style_h2 = ParagraphStyle(
    "h2",
    parent=styles["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=16,
    leading=20,
    textColor=GOLD_300,
    alignment=TA_LEFT,
    spaceBefore=18,
    spaceAfter=8,
)
style_h3 = ParagraphStyle(
    "h3",
    parent=styles["Heading3"],
    fontName="Helvetica-Bold",
    fontSize=12,
    leading=15,
    textColor=BONE,
    alignment=TA_LEFT,
    spaceBefore=10,
    spaceAfter=4,
)
style_body = ParagraphStyle(
    "body",
    parent=styles["BodyText"],
    fontName="Helvetica",
    fontSize=10,
    leading=14,
    textColor=PARCHMENT,
    alignment=TA_JUSTIFY,
    spaceAfter=6,
)
style_body_left = ParagraphStyle(
    "body_left",
    parent=style_body,
    alignment=TA_LEFT,
)
style_meta = ParagraphStyle(
    "meta",
    parent=styles["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=9,
    leading=12,
    textColor=SILT,
    spaceAfter=4,
)
style_question = ParagraphStyle(
    "question",
    parent=style_body,
    fontName="Helvetica",
    fontSize=10.5,
    leading=15,
    leftIndent=14,
    spaceAfter=10,
    textColor=BONE,
)
style_question_num = ParagraphStyle(
    "question_num",
    parent=styles["Heading4"],
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=14,
    textColor=GOLD_400,
    spaceBefore=6,
    spaceAfter=2,
)
style_callout = ParagraphStyle(
    "callout",
    parent=style_body,
    fontName="Helvetica",
    fontSize=10,
    leading=14,
    textColor=BONE,
    backColor=INK_700,
    borderColor=GOLD_700,
    borderWidth=0.5,
    borderPadding=8,
    leftIndent=0,
    rightIndent=0,
)


# ---------- Doc template ----------
def build_doc():
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=1.6 * cm,
        title="DeathRoll Luck v1 — Economics Proposal",
        author="Aleix",
        subject="Game Economics & House Edge — Collaborator Review",
        creator="Aleix",
        producer="Aleix",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="normal",
    )
    template = PageTemplate(id="deathroll", frames=frame, onPage=draw_background)
    doc.addPageTemplates([template])
    return doc


# ---------- Tables ----------
def header_block():
    elems = []
    elems.append(Paragraph("DeathRoll Luck", style_h1))
    elems.append(
        Paragraph(
            f"Game Economics Proposal — v1 — {date.today().isoformat()}",
            style_h1_sub,
        )
    )
    elems.append(
        Paragraph(
            "Document for collaborator review. Validate the open points "
            "in section 5 (\"Open Questions\") and confirm before we lock the "
            "v1 spec.",
            style_meta,
        )
    )
    elems.append(Spacer(1, 8))
    return elems


def section_currency():
    elems = [Paragraph("1. Currency &amp; Bet Limits", style_h2)]
    elems.append(
        Paragraph(
            "<b>Currency:</b> WoW Gold (denoted <b>G</b>). Stored in the "
            "database as <font face='Courier' color='#FFD96B'>BIGINT</font> "
            "(whole gold, no decimals). Inputs require <b>exact numbers</b> — "
            "no <font face='Courier'>k</font> / "
            "<font face='Courier'>m</font> / "
            "<font face='Courier'>b</font> suffixes (consistency and "
            "typo-prevention prioritised over keystroke savings).",
            style_body,
        )
    )

    data = [
        ["Parameter", "Default Value", "Notes"],
        ["Min bet (per game)", "100 G", "Admin-configurable in DB"],
        ["Max bet (per game)", "500,000 G", "Admin-configurable in DB"],
        [
            "Storage type",
            "BIGINT",
            "No floating point in money math",
        ],
        [
            "Input format",
            "exact integer",
            "Strict regex, rejects suffixes/separators",
        ],
        [
            "Display format",
            "1,234,567 G",
            "Localised separators in embeds (read-only)",
        ],
    ]
    t = Table(data, colWidths=[5 * cm, 4 * cm, 8 * cm], hAlign="LEFT")
    t.setStyle(table_style_default())
    elems.append(t)
    return elems


def section_payouts():
    elems = [Paragraph("2. Per-Game Payouts &amp; House Edge", style_h2)]
    elems.append(
        Paragraph(
            "Proposal aligned with online-casino industry norms (1–5%) and "
            "comparable to RuneLuck. Every value below is admin-configurable "
            "from the database via slash commands and audit-logged on change.",
            style_body,
        )
    )

    # Wrap text-heavy cells in Paragraph so they wrap inside their column.
    cell_style = ParagraphStyle(
        "cell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=10.5,
        textColor=PARCHMENT,
    )
    payout_style = ParagraphStyle(
        "cell_payout",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=GOLD_300,
        alignment=TA_LEFT,
    )
    edge_style = ParagraphStyle(
        "cell_edge",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=EMBER_500,
        alignment=TA_LEFT,
    )

    def P(text, style=cell_style):
        return Paragraph(text, style)

    data = [
        ["Game", "Type", "Mechanic", "Payout", "Edge", "Notes"],
        [
            "Coinflip",
            "PvE",
            P("Heads / Tails"),
            P("1.96x", payout_style),
            P("2 %", edge_style),
            P("Even-money classic"),
        ],
        [
            "Dice",
            "PvE",
            P("Roll 1–100, Over/Under"),
            P("dynamic", payout_style),
            P("2 %", edge_style),
            P("Player picks threshold, multiplier auto-adjusts"),
        ],
        [
            "99x",
            "PvE",
            P("Pick exact 1–100"),
            P("99x", payout_style),
            P("1 %", edge_style),
            P("Lowest edge, lowest hit rate"),
        ],
        [
            "Hot/Cold",
            "PvE",
            P("Hot / Cold / Rainbow"),
            P("1.96x · 15x", payout_style),
            P("~3 %", edge_style),
            P("Rainbow ~5% probability"),
        ],
        [
            "Mines",
            "PvE",
            P("5×5 grid, N mines, cash-out anytime"),
            P("combinatorial × 0.98", payout_style),
            P("2 %", edge_style),
            P("Multiplier grows per safe tile"),
        ],
        [
            "Blackjack",
            "Casino",
            P("Vegas rules: S17, BJ 3:2, double"),
            P("1x / 1.5x BJ", payout_style),
            P("~0.5 %", edge_style),
            P("Lowest edge — high-roller draw"),
        ],
        [
            "Roulette",
            "Casino",
            P("European single-zero"),
            P("35x / 2x", payout_style),
            P("2.7 %", edge_style),
            P("Single-zero standard"),
        ],
        [
            "Flower Poker",
            "Duel",
            P("5 flowers vs bot, best hand wins"),
            P("1.95x", payout_style),
            P("5 %", edge_style),
            P("Tie = full refund"),
        ],
        [
            "Dice Duel",
            "Duel",
            P("Higher roll vs bot"),
            P("1.95x", payout_style),
            P("5 %", edge_style),
            P("Tie = re-roll"),
        ],
        [
            "Staking Duel",
            "Duel",
            P("OSRS-style HP/dmg combat sim vs bot"),
            P("1.95x", payout_style),
            P("5 %", edge_style),
            P("Multi-round visualisation"),
        ],
    ]
    # Total width must fit page (A4 - 4cm margins = 17 cm). Sum below = 17.0 cm.
    col_widths = [2.5 * cm, 1.4 * cm, 4.6 * cm, 2.7 * cm, 1.3 * cm, 4.5 * cm]
    t = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(table_style_payouts())
    elems.append(t)
    return elems


def section_raffle():
    elems = [Paragraph("3. 5B Casino Raffle (rake-funded)", style_h2)]
    bullets = [
        "<b>Funding model:</b> rake — a percentage of every bet's volume is routed to the raffle pool.",
        "<b>Frequency:</b> monthly draw.",
        "<b>Tickets (proposed):</b> 1 ticket per 10,000 G wagered (threshold scaled down from RuneLuck's 100M GP for the WoW gold range — to be confirmed).",
        "<b>Top 3 prize split:</b> 50 / 30 / 20 % of the accumulated monthly pool.",
        "<b>Provably Fair:</b> winner selection uses the same HMAC-SHA512 system; the active server seed is revealed at draw time.",
    ]
    for b in bullets:
        elems.append(Paragraph(f"&#8226;&nbsp;&nbsp;{b}", style_body))
    return elems


def section_cross_cutting():
    elems = [Paragraph("4. Cross-Cutting Systems", style_h2)]
    bullets = [
        "<b>Provably Fair:</b> every game, every round, every raffle draw uses HMAC-SHA512 over (server seed + client seed + nonce) with a strict commit/reveal cycle. An open-source verifier will be published so any user can re-compute outcomes locally.",
        "<b>Bet limits storage:</b> table <font face='Courier'>game_config(game_name, min_bet, max_bet, house_edge, payout_multiplier, enabled, updated_at, updated_by)</font>. Admin slash commands write to this table and audit-log every change.",
        "<b>Rate limit (proposed):</b> 1 bet every 2 seconds, per user, per game.",
        "<b>Concurrent stake cap (proposed):</b> TBD — to be set after observing real traffic patterns.",
        "<b>Money operations:</b> all transactions wrapped in <font face='Courier'>SELECT ... FOR UPDATE</font> row-locks, idempotency-keyed, written to an append-only audit log.",
        "<b>Security posture:</b> fintech-grade. No exposed Postgres port, secrets never logged, dependencies pinned and audited, mypy strict on critical modules, ≥ 90% coverage on balance and fairness logic.",
    ]
    for b in bullets:
        elems.append(Paragraph(f"&#8226;&nbsp;&nbsp;{b}", style_body))
    return elems


def section_open_questions():
    elems = [Paragraph("5. Open Questions for Collaborator", style_h2)]
    elems.append(
        Paragraph(
            "Please review and respond to the following before we lock v1. "
            "Each item has either a yes/no answer or a number to confirm/adjust.",
            style_body,
        )
    )

    questions = [
        (
            "1. House edges per game",
            "Do the proposed house edges (Coinflip 2%, Dice 2%, 99x 1%, Hot/Cold ~3%, "
            "Mines 2%, Blackjack ~0.5%, Roulette 2.7%, Flower Poker 5%, Dice Duel 5%, "
            "Staking Duel 5%) look correct? Or should any be raised/lowered? "
            "Please call out any specific game you want adjusted and the target edge.",
        ),
        (
            "2. Raffle ticket threshold",
            "Proposal is <b>1 ticket per 10,000 G wagered</b> (scaled down from "
            "RuneLuck's 100M GP threshold to fit our 100–500,000 G bet range). "
            "Confirm or propose a different threshold.",
        ),
        (
            "3. Raffle rake percentage",
            "What percentage of every bet's volume should be diverted to the "
            "raffle pool? Default proposed: <b>1%</b>. "
            "Higher % grows the pool faster but reduces house take.",
        ),
        (
            "4. Tie behaviour in duel games",
            "<b>Flower Poker</b> on tie → full refund. "
            "<b>Dice Duel</b> on tie → automatic re-roll. "
            "<b>Staking Duel</b> on tie → re-roll. "
            "OK, or change any of these?",
        ),
        (
            "5. Roulette variant",
            "European single-zero (~2.7% edge, fairer, industry preferred for online "
            "fair casinos) vs American double-zero (~5.26% edge, larger house take). "
            "Default proposed: <b>European</b>.",
        ),
        (
            "6. Blackjack rules",
            "Default proposed: stand on soft 17, BJ pays 3:2, double-down allowed, "
            "split allowed, insurance allowed (edge stays ~0.5% with perfect play). "
            "OK, or restrict any of these?",
        ),
        (
            "7. Game naming &amp; theming",
            "RuneLuck uses OSRS-themed names (\"Flower Poker\", \"Staking Duel\"). "
            "Should we re-theme any to fit WoW lore? Examples: \"Hot/Cold\" → "
            "\"Light/Shadow\" or \"Alliance/Horde\"; \"Flower Poker\" → \"Herb Poker\"; "
            "\"Staking Duel\" → \"Arena Duel\" or \"Duelist Stake\". "
            "Pick what stays vs what is renamed (and proposed new name).",
        ),
    ]

    for title, body in questions:
        block = [
            Paragraph(title, style_question_num),
            Paragraph(body, style_question),
        ]
        elems.append(KeepTogether(block))

    elems.append(Spacer(1, 12))
    elems.append(
        Paragraph(
            "<b>Reply format suggestion:</b> for each numbered point, write "
            "<i>OK</i> or your adjustment in one short line. We will lock the "
            "spec immediately after your response.",
            style_callout,
        )
    )
    return elems


# ---------- Table styles ----------
def table_style_default():
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), INK_700),
            ("TEXTCOLOR", (0, 0), (-1, 0), GOLD_400),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BACKGROUND", (0, 1), (-1, -1), INK_900),
            ("TEXTCOLOR", (0, 1), (-1, -1), PARCHMENT),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 9.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("LINEBELOW", (0, 0), (-1, 0), 1, GOLD_500),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, INK_600),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [INK_900, INK_800]),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ]
    )


def table_style_payouts():
    # Note: payout and edge columns now use Paragraph cells with their own
    # color/font, so we don't override them here.
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), INK_700),
            ("TEXTCOLOR", (0, 0), (-1, 0), GOLD_400),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9.5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BACKGROUND", (0, 1), (-1, -1), INK_900),
            ("TEXTCOLOR", (0, 1), (-1, -1), PARCHMENT),
            ("FONTNAME", (0, 1), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("LINEBELOW", (0, 0), (-1, 0), 1, GOLD_500),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, INK_600),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [INK_900, INK_800]),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ]
    )


# ---------- Build ----------
def main():
    doc = build_doc()
    story = []
    story.extend(header_block())
    story.extend(section_currency())
    story.extend(section_payouts())
    story.extend(section_raffle())
    story.extend(section_cross_cutting())
    story.extend(section_open_questions())
    doc.build(story)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
