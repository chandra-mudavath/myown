"""chat: internal chat, groups, case chats with clients, client queries

- account_types.is_internal (STAFF, ADMIN, HR = true): who may use internal chat.
- chat_query_topics lookup, seeded with the topics a client picks when asking a question.
- chat_threads, chat_participants, chat_messages, chat_attachments.
- id_sequences row CHAT_THREAD (MSG-0000001).

One CASE thread per case is enforced in the service layer. On PostgreSQL and SQLite a
partial unique index backs it up; MySQL has no partial indexes.

Revision ID: a7c9e1b3d5f8
Revises: f6a8c0e2b4d7
Create Date: 2026-10-02

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a7c9e1b3d5f8'
down_revision = 'f6a8c0e2b4d7'
branch_labels = None
depends_on = None


INTERNAL_ACCOUNT_TYPES = ('STAFF', 'ADMIN', 'HR')

TOPICS = [
    ('FILING_STATUS', 'Filing status'),
    ('DOCUMENTS', 'Documents'),
    ('PAYMENT', 'Payment & invoice'),
    ('REFUND', 'Refund'),
    ('IRS_NOTICE', 'IRS notice'),
    ('GENERAL', 'Other question'),
]

# Creation order respects foreign keys; downgrade drops in reverse.
TABLES = ['chat_query_topics', 'chat_threads', 'chat_participants', 'chat_messages', 'chat_attachments']


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _account_fk(name, nullable=True, ondelete=None):
    return sa.Column(name, sa.String(length=36), sa.ForeignKey('auth_accounts.id', ondelete=ondelete), nullable=nullable)


def _define(name):
    """op.create_table arguments for one table, matching app/models/chat.py and schema.sql."""
    if name == 'chat_query_topics':
        return [
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code', sa.String(length=30), nullable=False, unique=True),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('description', sa.String(length=255), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            _ts('created_at'),
            _ts('updated_at'),
        ]
    if name == 'chat_threads':
        return [
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('thread_number', sa.String(length=20), nullable=False, unique=True),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=True),
            sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=True),
            sa.Column('topic_id', sa.Integer(), sa.ForeignKey('chat_query_topics.id'), nullable=True),
            sa.Column('subject', sa.String(length=150), nullable=True),
            sa.Column('direct_key', sa.String(length=110), nullable=True, unique=True),
            sa.Column('status', sa.String(length=10), nullable=False),
            _account_fk('owner_account_id', ondelete='SET NULL'),
            _account_fk('created_by_account_id', nullable=False),
            _ts('last_message_at', nullable=True),
            sa.Column('last_message_preview', sa.String(length=160), nullable=True),
            _ts('resolved_at', nullable=True),
            _account_fk('resolved_by_account_id', ondelete='SET NULL'),
            _ts('hide_after', nullable=True),
            _ts('hidden_at', nullable=True),
            _ts('created_at'),
            _ts('updated_at'),
            sa.CheckConstraint("kind IN ('DIRECT', 'GROUP', 'CASE', 'QUERY')", name='ck_ct_kind'),
            sa.CheckConstraint("status IN ('open', 'resolved', 'closed')", name='ck_ct_status'),
            sa.CheckConstraint(
                "(kind = 'DIRECT' AND direct_key IS NOT NULL AND topic_id IS NULL)"
                " OR (kind = 'GROUP' AND name IS NOT NULL AND topic_id IS NULL AND direct_key IS NULL)"
                " OR (kind = 'CASE' AND case_id IS NOT NULL AND direct_key IS NULL AND topic_id IS NULL)"
                " OR (kind = 'QUERY' AND topic_id IS NOT NULL AND subject IS NOT NULL AND direct_key IS NULL)",
                name='ck_ct_shape',
            ),
        ]
    if name == 'chat_participants':
        return [
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('thread_id', sa.String(length=36), sa.ForeignKey('chat_threads.id', ondelete='CASCADE'), nullable=False),
            _account_fk('account_id', nullable=False),
            sa.Column('member_role', sa.String(length=10), nullable=False),
            sa.Column('added_reason', sa.String(length=20), nullable=False),
            _ts('joined_at'),
            _ts('left_at', nullable=True),
            _ts('last_read_at', nullable=True),
            sa.Column('is_muted', sa.Boolean(), nullable=False),
            sa.UniqueConstraint('thread_id', 'account_id', name='uq_cp_thread_account'),
            sa.CheckConstraint("member_role IN ('owner', 'member')", name='ck_cp_role'),
            sa.CheckConstraint(
                "added_reason IN ('member', 'client', 'case_assignment', 'admin_joined', 'query_owner')",
                name='ck_cp_reason',
            ),
        ]
    if name == 'chat_messages':
        return [
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('thread_id', sa.String(length=36), sa.ForeignKey('chat_threads.id', ondelete='CASCADE'), nullable=False),
            _account_fk('sender_account_id'),
            sa.Column('sender_type', sa.String(length=30), nullable=True),
            sa.Column('sender_name', sa.String(length=80), nullable=True),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('visibility', sa.String(length=10), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('reply_to_message_id', sa.String(length=36), sa.ForeignKey('chat_messages.id'), nullable=True),
            _ts('edited_at', nullable=True),
            _ts('deleted_at', nullable=True),
            _account_fk('deleted_by_account_id'),
            _ts('created_at'),
            sa.CheckConstraint("kind IN ('text', 'system')", name='ck_cm_kind'),
            sa.CheckConstraint("visibility IN ('all', 'internal')", name='ck_cm_visibility'),
        ]
    if name == 'chat_attachments':
        return [
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('message_id', sa.String(length=36), sa.ForeignKey('chat_messages.id', ondelete='CASCADE'), nullable=False),
            sa.Column('original_filename', sa.String(length=255), nullable=False),
            sa.Column('stored_filename', sa.String(length=255), nullable=False),
            sa.Column('file_path', sa.String(length=500), nullable=False),
            sa.Column('mime_type', sa.String(length=100), nullable=False),
            sa.Column('file_size', sa.Integer(), nullable=False),
            sa.Column('filing_document_id', sa.String(length=36), sa.ForeignKey('filing_documents.id', ondelete='SET NULL'), nullable=True),
            _ts('created_at'),
        ]
    raise KeyError(name)


# table -> [(index name, columns, unique)]
INDEXES = {
    'chat_threads': [
        ('ix_chat_threads_case', ['case_id', 'kind'], False),
        ('ix_chat_threads_queue', ['kind', 'status', 'owner_account_id'], False),
        ('ix_chat_threads_hide', ['hidden_at', 'hide_after'], False),
    ],
    'chat_participants': [('ix_chat_participants_inbox', ['account_id', 'left_at'], False)],
    'chat_messages': [('ix_chat_messages_thread', ['thread_id', 'created_at'], False)],
    'chat_attachments': [('ix_chat_attachments_message_id', ['message_id'], False)],
}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    now = datetime.now(timezone.utc)

    # account_types.is_internal. create_all() never adds columns to an existing table.
    if 'is_internal' not in {c['name'] for c in insp.get_columns('account_types')}:
        with op.batch_alter_table('account_types') as batch:
            batch.add_column(sa.Column('is_internal', sa.Boolean(), nullable=False, server_default=sa.false()))
    bind.execute(
        sa.text('UPDATE account_types SET is_internal = :yes WHERE code IN :codes').bindparams(
            sa.bindparam('codes', expanding=True)),
        {'yes': True, 'codes': list(INTERNAL_ACCOUNT_TYPES)},
    )

    # app/main.py runs create_all() on startup, so any of these tables may already exist (empty).
    for name in TABLES:
        if insp.has_table(name):
            continue
        op.create_table(name, *_define(name))
        for index_name, columns, unique in INDEXES.get(name, []):
            op.create_index(index_name, name, columns, unique=unique)

    existing_indexes = {i['name'] for i in sa.inspect(bind).get_indexes('chat_threads')}
    if bind.dialect.name in ('postgresql', 'sqlite') and 'uq_chat_threads_case' not in existing_indexes:
        op.create_index('uq_chat_threads_case', 'chat_threads', ['case_id'], unique=True,
                        postgresql_where=sa.text("kind = 'CASE'"), sqlite_where=sa.text("kind = 'CASE'"))

    existing_topics = {r[0] for r in bind.execute(sa.text('SELECT code FROM chat_query_topics'))}
    next_id = (bind.execute(sa.text('SELECT MAX(id) FROM chat_query_topics')).scalar() or 0) + 1
    missing = [(i, c, n) for i, (c, n) in enumerate(TOPICS) if c not in existing_topics]
    rows = [dict(id=next_id + k, code=c, name=n, sort_order=i + 1, is_active=True, created_at=now, updated_at=now)
            for k, (i, c, n) in enumerate(missing)]
    if rows:
        op.bulk_insert(sa.table('chat_query_topics', *[sa.column(c) for c in rows[0]]), rows)

    has_counter = bind.execute(sa.text("SELECT 1 FROM id_sequences WHERE name = 'CHAT_THREAD'")).first()
    if not has_counter:
        bind.execute(sa.text(
            "INSERT INTO id_sequences (name, prefix, width, current_value, updated_at) "
            "VALUES ('CHAT_THREAD', 'MSG-', 7, 0, :now)"), {'now': now})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM id_sequences WHERE name = 'CHAT_THREAD'"))
    for name in reversed(TABLES):
        op.drop_table(name)
    with op.batch_alter_table('account_types') as batch:
        batch.drop_column('is_internal')
