"""Default-deny access tiers (architecture P2, INV-9).

The blind first read is only meaningful if nothing else can be read first. The
old guard enumerated the commands to block, so `html`, `export` and `audit` —
each of which exposes the contract, the ledger and every committed chapter —
were never listed and stayed open while a sealed chapter_review was pending and
unsealed. Enumerating what is forbidden always leaks; enumerating what is
allowed fails closed.

`COMMAND_LEVEL` below is the single registry. A command absent from it is
refused, and the architecture test asserts the table covers every CLI
subcommand, so a new command cannot ship unguarded.

Layering: L3. `reviewflow` is imported inside the function on purpose: the
policy layer must not become an import-cycle participant at module load.
"""

PUBLIC = 'public'   # 不接触正文、账本、设计或他人产物
SEALED = 'sealed'   # 接触上述内容；存在未冻结首读的待审稿任务时禁止

# `revise`, `replan`, `pause` and friends also touch the ledger, but every one
# of them already refuses to run while a task is pending (engine.begin_revision
# and friends), so they cannot be reached during a seal. They stay PUBLIC to
# preserve their specific error codes; the reason is recorded here so a future
# reader does not mistake the tier for a judgement about their content.
COMMAND_LEVEL = {
    # --- public --------------------------------------------------------
    'init': PUBLIC, 'status': PUBLIC, 'next': PUBLIC, 'advance': PUBLIC,
    'submit': PUBLIC, 'preflight': PUBLIC, 'measure': PUBLIC,
    'pause': PUBLIC, 'resume': PUBLIC, 'complete': PUBLIC, 'replan': PUBLIC,
    'doctor': PUBLIC, 'tidy': PUBLIC, 'checkpoint': PUBLIC,
    'approve': PUBLIC, 'pilot': PUBLIC, 'first-read': PUBLIC,
    'revise': PUBLIC, 'evidence': PUBLIC, 'dependencies': PUBLIC,
    # --- sealed --------------------------------------------------------
    'recall': SEALED, 'map': SEALED, 'design': SEALED, 'character': SEALED,
    'candidate': SEALED, 'material': SEALED, 'impact': SEALED, 'patch': SEALED,
    'audit': SEALED, 'html': SEALED, 'export': SEALED,
    'task-view': SEALED,
    # Reading configuration exposes the contract, the style anchor and the
    # publication boundary; writing it is a governance act by author/controller.
    'config': lambda args: SEALED if getattr(args, 'value', None) is None else PUBLIC,
}


def level_of(command, args=None):
    """Tier of a command. Absent from the registry means refused (P2)."""
    from .store import require
    entry = COMMAND_LEVEL.get(command)
    require(entry is not None, 'access', '命令未在访问分级表中登记，默认拒绝',
            {'command': command,
             'recover': '在 scripts/novel_core/access.py 的 COMMAND_LEVEL 登记级别后再放行'})
    return entry(args) if callable(entry) else entry


def guard(db, command, args=None):
    """Refuse sealed reads while a chapter_review awaits its frozen first read."""
    if level_of(command, args) != SEALED:
        return
    from .reviewflow import require_material_access
    require_material_access(db)
