"""Default game_config + global_config rows for Luck v1.

Revision ID: 0021_luck_seed_defaults
Revises: 0020_luck_schemas
Create Date: 2026-05-04

Implements Luck plan Story 2.9 + design §3.3 economic constants:

- Inserts one row in ``luck.game_config`` per playable game (9
  total; Flower Poker explicitly excluded per spec §0):
    - coinflip, dice, ninetyninex, hotcold, mines,
      blackjack, roulette, diceduel, stakingduel.
- Every game gets ``house_edge_bps = 500`` (uniform 5 % target),
  ``min_bet = 100``, ``max_bet = 500_000``, ``enabled = TRUE``.
- Game-specific knobs land in ``extra_config`` JSONB:
    - blackjack: ``{"commission_bps": 450,
                    "rules": "vegas_s17_3to2_noins_nosplit",
                    "decks": 6}`` — the 4.5 % commission brings
                  vegas-rules BJ to the 5 % edge target.
    - roulette: ``{"commission_bps": 236,
                   "variant": "european_single_zero"}`` — 2.36 %
                  commission brings european-single-zero to the
                  5 % target (its native edge is ~2.7 %).
    - mines: ``{"max_mines": 24, "min_mines": 1,
                "default_mines": 3, "grid_size": 25}``.
    - All other games: ``{}``.

- Inserts the four global_config rows that govern raffle and
  rate-limit behaviour:
    - ``raffle_rake_bps``           = 100   (1 % of every bet)
    - ``raffle_ticket_threshold_g`` = 100   (1 ticket per 100 G wagered)
    - ``bet_rate_limit_per_60s``    = 30
    - ``command_rate_limit_per_60s``= 30

Idempotent: every INSERT uses ``ON CONFLICT DO NOTHING`` keyed on
the table's PK. Re-running the migration after a partial run (or
after operator hand-edits) doesn't duplicate or overwrite. To
update a row's value, the operator uses
``UPDATE luck.game_config SET ... WHERE game_name = ...`` (or
``/admin set-bet-limits`` once Epic 11 lands), not this migration.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0021_luck_seed_defaults"
down_revision = "0020_luck_schemas"
branch_labels = None
depends_on = None


# The 9 playable games. ``actor_id = 0`` is the system actor (the
# treasury / sentinel user); audit-log rows for the seed insert
# would attribute it to the same id.
_GAME_DEFAULTS = (
    ("coinflip",    "{}"),
    ("dice",        "{}"),
    ("ninetyninex", "{}"),
    ("hotcold",     "{}"),
    ("mines",       '{"max_mines":24,"min_mines":1,"default_mines":3,"grid_size":25}'),
    ("blackjack",   '{"commission_bps":450,"rules":"vegas_s17_3to2_noins_nosplit","decks":6}'),
    ("roulette",    '{"commission_bps":236,"variant":"european_single_zero"}'),
    ("diceduel",    "{}"),
    ("stakingduel", "{}"),
)


def upgrade() -> None:
    # luck.game_config seeds.
    #
    # We use sa.text().bindparams() rather than f-string interpolation
    # because the ``extra_config`` JSON values contain ``:`` characters
    # (e.g. ``{"max_mines":24,...}``) which SQLAlchemy would otherwise
    # interpret as named bind parameter placeholders. Casting to JSONB
    # via ``CAST(:extra AS jsonb)`` lets us pass the JSON safely as a
    # text parameter.
    insert_game = sa.text(
        """
        INSERT INTO luck.game_config
          (game_name, enabled, min_bet, max_bet, house_edge_bps,
           extra_config, updated_by)
        VALUES
          (:game_name, TRUE, 100, 500000, 500,
           CAST(:extra AS jsonb), 0)
        ON CONFLICT (game_name) DO NOTHING
        """
    )
    for game_name, extra_json in _GAME_DEFAULTS:
        op.execute(insert_game.bindparams(game_name=game_name, extra=extra_json))

    # luck.global_config seeds. The PK is `key`; ON CONFLICT
    # preserves any operator-tuned value during re-runs.
    op.execute(
        """
        INSERT INTO luck.global_config (key, value_int, updated_by) VALUES
            ('raffle_rake_bps',            100, 0),
            ('raffle_ticket_threshold_g',  100, 0),
            ('bet_rate_limit_per_60s',     30,  0),
            ('command_rate_limit_per_60s', 30,  0)
        ON CONFLICT (key) DO NOTHING;
        """
    )


def downgrade() -> None:
    # Remove only the rows this migration inserted. Operator-added
    # game_config rows (e.g., for a later v1.x game) are NOT touched.
    games = ", ".join(f"'{g}'" for g, _ in _GAME_DEFAULTS)
    op.execute(f"DELETE FROM luck.game_config WHERE game_name IN ({games});")

    op.execute("""
        DELETE FROM luck.global_config WHERE key IN (
            'raffle_rake_bps',
            'raffle_ticket_threshold_g',
            'bet_rate_limit_per_60s',
            'command_rate_limit_per_60s'
        );
    """)
