==== catalogs
CREATE TABLE catalogs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  space_id   INTEGER REFERENCES spaces(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  parent_id  INTEGER REFERENCES catalogs(id) ON DELETE CASCADE,
  module     TEXT NOT NULL DEFAULT 'question',
  sort       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
==== courseware
CREATE TABLE courseware (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        space_id    INTEGER REFERENCES spaces(id) ON DELETE CASCADE,
        catalog_id  INTEGER REFERENCES catalogs(id) ON DELETE SET NULL,
        name        TEXT    NOT NULL,
        subtitle    TEXT    NOT NULL DEFAULT '',
        category    TEXT    NOT NULL DEFAULT 'lecture',
        status      TEXT    NOT NULL DEFAULT 'draft',
        revision    INTEGER NOT NULL DEFAULT 1,
        config_json TEXT    NOT NULL DEFAULT '{}',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
      )
==== courseware_block_assets
CREATE TABLE courseware_block_assets (
        block_id   INTEGER NOT NULL REFERENCES courseware_blocks(id) ON DELETE CASCADE,
        asset_id   INTEGER NOT NULL REFERENCES paper_assets(id) ON DELETE CASCADE,
        source_ref TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (block_id, asset_id, source_ref)
      )
==== courseware_blocks
CREATE TABLE courseware_blocks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        courseware_id INTEGER NOT NULL REFERENCES courseware(id) ON DELETE CASCADE,
        section_id    INTEGER NOT NULL REFERENCES courseware_sections(id) ON DELETE CASCADE,
        -- question / text / heading / image
        kind          TEXT    NOT NULL DEFAULT 'question',
        -- 题目块专用；原题删除后置 NULL，快照仍可打印（同 paper_items）。
        question_id   INTEGER REFERENCES questions(id) ON DELETE SET NULL,
        position      INTEGER NOT NULL DEFAULT 0,
        -- 非题目块的正文（text/heading 的文字、image 的 markdown 引用）。
        body          TEXT    NOT NULL DEFAULT '',
        -- 题目块的冻结快照，结构同 PaperQuestionSnapshotV1；其余种类为 '{}'。
        snapshot_json TEXT    NOT NULL DEFAULT '{}',
        content_hash  TEXT    NOT NULL DEFAULT '',
        -- 卷内那套显示开关的课件版：是否印答案/解析。
        show_answer   INTEGER NOT NULL DEFAULT 1,
        show_analysis INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT    NOT NULL DEFAULT (datetime('now')), env_name TEXT, env_title TEXT NOT NULL DEFAULT '',
        UNIQUE (section_id, position)
      )
==== courseware_sections
CREATE TABLE courseware_sections (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        courseware_id INTEGER NOT NULL REFERENCES courseware(id) ON DELETE CASCADE,
        title         TEXT    NOT NULL DEFAULT '',
        instructions  TEXT    NOT NULL DEFAULT '',
        show_title    INTEGER NOT NULL DEFAULT 1,
        export_visible INTEGER NOT NULL DEFAULT 1,
        position      INTEGER NOT NULL DEFAULT 0
      )
==== images
CREATE TABLE images (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  space_id      INTEGER REFERENCES spaces(id) ON DELETE CASCADE,
  catalog_id    INTEGER REFERENCES catalogs(id) ON DELETE SET NULL,
  filename      TEXT NOT NULL,
  original_name TEXT NOT NULL DEFAULT '',
  mime          TEXT NOT NULL DEFAULT 'image/png',
  size          INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)
==== import_batch_catalogs
CREATE TABLE import_batch_catalogs (
        batch_id           TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
        plan_key           TEXT NOT NULL,
        original_catalog_id INTEGER NOT NULL,
        catalog_id         INTEGER REFERENCES catalogs(id) ON DELETE SET NULL,
        disposition        TEXT NOT NULL CHECK (disposition IN ('created', 'reused')),
        imported_name      TEXT NOT NULL,
        parent_plan_key    TEXT,
        imported_hash      TEXT NOT NULL,
        PRIMARY KEY (batch_id, plan_key)
      )
==== import_batch_images
CREATE TABLE import_batch_images (
        batch_id         TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
        plan_key         TEXT NOT NULL,
        original_image_id INTEGER NOT NULL,
        image_id         INTEGER REFERENCES images(id) ON DELETE SET NULL,
        disposition      TEXT NOT NULL CHECK (disposition IN ('created', 'reused')),
        filename         TEXT NOT NULL,
        original_name    TEXT NOT NULL,
        imported_hash    TEXT NOT NULL, ownership_version INTEGER NOT NULL DEFAULT 0, imported_state_json TEXT,
        PRIMARY KEY (batch_id, plan_key)
      )
==== import_batch_questions
CREATE TABLE import_batch_questions (
        batch_id             TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
        plan_key             TEXT NOT NULL,
        original_question_id INTEGER NOT NULL,
        question_id          INTEGER REFERENCES questions(id) ON DELETE SET NULL,
        catalog_plan_key     TEXT NOT NULL,
        imported_hash        TEXT NOT NULL,
        PRIMARY KEY (batch_id, plan_key)
      )
==== import_batches
CREATE TABLE import_batches (
        id                    TEXT PRIMARY KEY,
        format                TEXT NOT NULL,
        source_path           TEXT NOT NULL,
        source_name           TEXT NOT NULL,
        source_sha256         TEXT NOT NULL,
        source_size           INTEGER NOT NULL,
        source_mtime_ms       INTEGER NOT NULL,
        plan_hash             TEXT NOT NULL,
        confirmed_plan_hash   TEXT,
        plan_confirmed_at     TEXT,
        preview_hash          TEXT NOT NULL,
        catalog_snapshot_hash TEXT NOT NULL,
        space_id              INTEGER REFERENCES spaces(id) ON DELETE SET NULL,
        metadata_json         TEXT NOT NULL DEFAULT '{}',
        plan_json             TEXT NOT NULL,
        report_path           TEXT NOT NULL,
        backup_path           TEXT NOT NULL,
        last_rollback_report_path TEXT,
        status                TEXT NOT NULL CHECK (
          status IN ('committed', 'partially_rolled_back', 'rolled_back')
        ),
        reimport_of           TEXT REFERENCES import_batches(id) ON DELETE SET NULL,
        created_at            TEXT NOT NULL DEFAULT (datetime('now')),
        committed_at          TEXT NOT NULL DEFAULT (datetime('now')),
        rolled_back_at        TEXT
      )
==== import_file_cleanup_queue
CREATE TABLE import_file_cleanup_queue (
        filename   TEXT PRIMARY KEY,
        batch_id   TEXT REFERENCES import_batches(id) ON DELETE SET NULL,
        reason     TEXT NOT NULL DEFAULT 'rollback',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      , expected_file_sha256 TEXT, expected_file_size INTEGER)
==== knowledge_card_links
CREATE TABLE knowledge_card_links (
        id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        -- 「这张卡」依赖 to_id 那张。方向是 from 依赖 to，即 to 是前置知识。
        from_id    INTEGER NOT NULL REFERENCES knowledge_cards(id) ON DELETE CASCADE,
        to_id      INTEGER NOT NULL REFERENCES knowledge_cards(id) ON DELETE CASCADE,
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(from_id, to_id)
      )
==== knowledge_card_segments
CREATE TABLE knowledge_card_segments (
        id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        card_id    INTEGER NOT NULL REFERENCES knowledge_cards(id) ON DELETE CASCADE,
        -- 段落类型的机器名，取值是 courseware/environments.ts 的子集（含 proof/assumption）。
        kind       TEXT    NOT NULL DEFAULT 'note',
        -- 段落标题：custom 的标签，或「证明（几何法）」这类限定语。空串 = 用类型默认标签。
        title      TEXT    NOT NULL DEFAULT '',
        -- 段落正文，与题面/卡片正文同一套语法，走同一个 latex-tokens 分词器。
        body       TEXT    NOT NULL DEFAULT '',
        -- 卡片内的顺序，0 起连续。无 UNIQUE，理由见上方注释。
        position   INTEGER NOT NULL DEFAULT 0,
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
      )
==== knowledge_cards
CREATE TABLE knowledge_cards (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        space_id   INTEGER REFERENCES spaces(id) ON DELETE CASCADE,
        catalog_id INTEGER REFERENCES catalogs(id) ON DELETE SET NULL,
        title      TEXT    NOT NULL,
        -- 卡片类型的机器名，取值是 courseware/environments.ts 的子集。
        -- 存机器名不存中文标签，理由同 courseware_env_blocks_v1。
        kind       TEXT    NOT NULL DEFAULT 'definition',
        -- 正文，支持 $...$ / $$...$$ / ![](){} / ```tikz，与题面同一套语法。
        body       TEXT    NOT NULL DEFAULT '',
        -- 一句话摘要，列表里显示；空串就退回截断正文，不强制填。
        summary    TEXT    NOT NULL DEFAULT '',
        -- 关联的题目知识点名；空串 = 不挂。
        kp_name    TEXT    NOT NULL DEFAULT '',
        revision   INTEGER NOT NULL DEFAULT 1,
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
      )
==== knowledge_points
CREATE TABLE knowledge_points (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
)
==== paper_assets
CREATE TABLE paper_assets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256      TEXT NOT NULL UNIQUE,
  mime        TEXT NOT NULL DEFAULT 'image/png',
  size        INTEGER NOT NULL DEFAULT 0,
  storage_key TEXT NOT NULL,
  -- upload = 用户的图，永不回收；tikz = 组卷时编译出来的临时 PNG，导出后回收。
  origin      TEXT NOT NULL DEFAULT 'upload',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
==== paper_generation_runs
CREATE TABLE paper_generation_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id      INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  seed          TEXT    NOT NULL DEFAULT '',
  rule_json     TEXT    NOT NULL DEFAULT '{}',
  status        TEXT    NOT NULL DEFAULT 'ok',
  diagnostics   TEXT    NOT NULL DEFAULT '[]',
  item_count    INTEGER NOT NULL DEFAULT 0,
  duration_ms   INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
)
==== paper_item_assets
CREATE TABLE paper_item_assets (
  item_id    INTEGER NOT NULL REFERENCES paper_items(id) ON DELETE CASCADE,
  asset_id   INTEGER NOT NULL REFERENCES paper_assets(id) ON DELETE CASCADE,
  source_ref TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (item_id, asset_id, source_ref)
)
==== paper_items
CREATE TABLE paper_items (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id          INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  section_id        INTEGER NOT NULL REFERENCES paper_sections(id) ON DELETE CASCADE,
  question_id       INTEGER REFERENCES questions(id) ON DELETE SET NULL,
  position          INTEGER NOT NULL DEFAULT 0,
  score             REAL    NOT NULL DEFAULT 0,
  solution_space_cm REAL    NOT NULL DEFAULT 0,
  figure_layout     TEXT    NOT NULL DEFAULT 'auto',
  locked            INTEGER NOT NULL DEFAULT 0,
  origin            TEXT    NOT NULL DEFAULT 'manual',
  render_overrides  TEXT    NOT NULL DEFAULT '{}',
  snapshot_json     TEXT    NOT NULL,
  content_hash      TEXT    NOT NULL DEFAULT '',
  created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE (section_id, position)
)
==== paper_sections
CREATE TABLE paper_sections (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  rule_key     TEXT,
  kind         TEXT    NOT NULL DEFAULT 'mixed',
  title        TEXT    NOT NULL DEFAULT '',
  instructions TEXT    NOT NULL DEFAULT '',
  show_title   INTEGER NOT NULL DEFAULT 1,
  position     INTEGER NOT NULL DEFAULT 0
, export_visible INTEGER NOT NULL DEFAULT 1)
==== papers
CREATE TABLE papers (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  space_id         INTEGER REFERENCES spaces(id) ON DELETE CASCADE,
  name             TEXT    NOT NULL,
  subtitle         TEXT    NOT NULL DEFAULT '',
  paper_type       TEXT    NOT NULL DEFAULT 'exam',
  duration_minutes INTEGER NOT NULL DEFAULT 120,
  status           TEXT    NOT NULL DEFAULT 'draft',
  revision         INTEGER NOT NULL DEFAULT 1,
  config_json      TEXT    NOT NULL DEFAULT '{}',
  last_rule_json   TEXT,
  last_seed        TEXT,
  created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
)
==== question_knowledge
CREATE TABLE question_knowledge (
  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  kp_id       INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  PRIMARY KEY (question_id, kp_id)
)
==== question_tags
CREATE TABLE question_tags (
  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (question_id, tag_id)
)
==== questions
CREATE TABLE questions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_id  INTEGER REFERENCES catalogs(id) ON DELETE SET NULL,
  type        TEXT NOT NULL DEFAULT 'answer',
  difficulty  INTEGER NOT NULL DEFAULT 3,
  title       TEXT NOT NULL DEFAULT '',
  number      TEXT NOT NULL DEFAULT '',
  year        INTEGER,
  stem        TEXT NOT NULL DEFAULT '',
  options     TEXT NOT NULL DEFAULT '[]',
  answer      TEXT NOT NULL DEFAULT '',
  analysis    TEXT NOT NULL DEFAULT '',
  analysis_methods TEXT NOT NULL DEFAULT '[]',
  translation TEXT NOT NULL DEFAULT '',
  confidence  REAL,
  source      TEXT NOT NULL DEFAULT '',
  region      TEXT NOT NULL DEFAULT '',
  video_url   TEXT NOT NULL DEFAULT '',
  remark      TEXT NOT NULL DEFAULT '',
  conditions  TEXT NOT NULL DEFAULT '[]',
  option_columns INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
==== schema_migrations
CREATE TABLE schema_migrations (
  id         TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
==== settings
CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
==== share_log
CREATE TABLE share_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  kind            TEXT NOT NULL DEFAULT 'single',
  payload_summary TEXT NOT NULL DEFAULT '',
  count           INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
==== shares
CREATE TABLE shares (
  token        TEXT PRIMARY KEY,
  kind         TEXT NOT NULL DEFAULT 'multi',
  title        TEXT NOT NULL DEFAULT '',
  question_ids TEXT NOT NULL DEFAULT '[]',
  space_id     INTEGER REFERENCES spaces(id) ON DELETE SET NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
==== spaces
CREATE TABLE spaces (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  tier       TEXT NOT NULL DEFAULT 'personal',
  readonly   INTEGER NOT NULL DEFAULT 0,
  sort       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
==== tags
CREATE TABLE tags (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
)
==== tikz_diagrams
CREATE TABLE tikz_diagrams (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        question_id INTEGER REFERENCES questions(id) ON DELETE SET NULL,
        space_id    INTEGER REFERENCES spaces(id) ON DELETE CASCADE,
        catalog_id  INTEGER REFERENCES catalogs(id) ON DELETE SET NULL,
        slot        TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
      , uuid TEXT)
==== tikz_file_cleanup_queue
CREATE TABLE tikz_file_cleanup_queue (
        filename TEXT PRIMARY KEY
      )
==== tikz_versions
CREATE TABLE "tikz_versions" (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          diagram_id        INTEGER NOT NULL REFERENCES tikz_diagrams(id) ON DELETE CASCADE,
          parent_version_id INTEGER REFERENCES "tikz_versions"(id) ON DELETE SET NULL,
          rendered_image_id INTEGER REFERENCES images(id) ON DELETE RESTRICT,
          reference_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
          code              TEXT NOT NULL,
          source_hash       TEXT NOT NULL,
          renderer_version  TEXT NOT NULL,
          generation_mode   TEXT NOT NULL,
          model             TEXT NOT NULL DEFAULT '',
          instruction       TEXT NOT NULL DEFAULT '',
          context_hash      TEXT NOT NULL DEFAULT '',
          render_width      INTEGER,
          render_height     INTEGER,
          created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        )