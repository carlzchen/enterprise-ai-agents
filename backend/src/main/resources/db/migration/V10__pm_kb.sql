-- 第三批：研发项目管理（类 Jira）/ 知识库（类 Confluence）

-- ========== 研发项目管理 ==========
CREATE TABLE pm_project (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_key    VARCHAR(10)  NOT NULL UNIQUE COMMENT '问题编号前缀，如 JM',
  name           VARCHAR(100) NOT NULL,
  plm_project_id BIGINT       COMMENT '关联 NPI 项目',
  lead_emp_id    BIGINT       NOT NULL,
  series         VARCHAR(20)  COMMENT '平台维护项目对应的产品系列（该系列客诉自动转研发缺陷）',
  board_type     VARCHAR(10)  NOT NULL COMMENT 'SCRUM 迭代 / KANBAN 看板',
  status         VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/ARCHIVED',
  issue_seq      INT          NOT NULL DEFAULT 0,
  created_date   DATE         NOT NULL
) COMMENT '研发项目';

CREATE TABLE pm_sprint (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id       BIGINT       NOT NULL,
  name             VARCHAR(50)  NOT NULL,
  goal             VARCHAR(200),
  start_date       DATE         NOT NULL,
  end_date         DATE         NOT NULL,
  status           VARCHAR(10)  NOT NULL COMMENT 'PLANNED/ACTIVE/CLOSED',
  committed_points INT          NOT NULL DEFAULT 0 COMMENT '启动时承诺的故事点',
  completed_points INT          NOT NULL DEFAULT 0 COMMENT '关闭时完成的故事点',
  INDEX idx_sprint_project (project_id)
) COMMENT '迭代';

CREATE TABLE pm_issue (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  issue_key          VARCHAR(20)  NOT NULL UNIQUE,
  project_id         BIGINT       NOT NULL,
  type               VARCHAR(10)  NOT NULL COMMENT 'EPIC/STORY/TASK/BUG',
  title              VARCHAR(200) NOT NULL,
  description        TEXT,
  status             VARCHAR(12)  NOT NULL COMMENT 'TODO/IN_PROGRESS/IN_REVIEW/DONE',
  resolution         VARCHAR(12)  COMMENT 'FIXED/WONT_FIX/DUPLICATE',
  priority           VARCHAR(8)   NOT NULL COMMENT 'HIGHEST/HIGH/MEDIUM/LOW',
  assignee_emp_id    BIGINT,
  reporter_emp_id    BIGINT       NOT NULL,
  sprint_id          BIGINT,
  epic_id            BIGINT,
  story_points       INT,
  estimate_hours     DECIMAL(6,1),
  labels             VARCHAR(200),
  ecn_id             BIGINT       COMMENT '关联工程变更',
  complaint_id       BIGINT       COMMENT '关联客诉（缺陷）',
  due_date           DATE,
  created_at         DATETIME     NOT NULL,
  started_at         DATETIME     COMMENT '首次进入进行中',
  resolved_at        DATETIME,
  INDEX idx_issue_project (project_id, status),
  INDEX idx_issue_sprint (sprint_id),
  INDEX idx_issue_assignee (assignee_emp_id, status),
  INDEX idx_issue_epic (epic_id)
) COMMENT '问题（需求 / 任务 / 缺陷 / 史诗）';

CREATE TABLE pm_issue_history (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  issue_id   BIGINT      NOT NULL,
  field      VARCHAR(20) NOT NULL COMMENT 'status / assignee / sprint / points',
  from_value VARCHAR(50),
  to_value   VARCHAR(50),
  changed_at DATETIME    NOT NULL,
  emp_id     BIGINT,
  INDEX idx_ih_issue (issue_id),
  INDEX idx_ih_time (changed_at)
) COMMENT '问题变更历史（燃尽图、周期时间、累积流图的数据源）';

CREATE TABLE pm_issue_link (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  from_issue_id BIGINT      NOT NULL,
  to_issue_id   BIGINT      NOT NULL,
  link_type     VARCHAR(12) NOT NULL COMMENT 'BLOCKS/RELATES/DUPLICATES',
  UNIQUE KEY uk_link (from_issue_id, to_issue_id, link_type)
) COMMENT '问题关联';

CREATE TABLE pm_comment (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  issue_id   BIGINT   NOT NULL,
  emp_id     BIGINT   NOT NULL,
  body       TEXT     NOT NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_pc_issue (issue_id)
) COMMENT '问题评论';

CREATE TABLE pm_worklog (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  issue_id  BIGINT       NOT NULL,
  emp_id    BIGINT       NOT NULL,
  work_date DATE         NOT NULL,
  hours     DECIMAL(5,2) NOT NULL,
  comment   VARCHAR(200),
  INDEX idx_wl_issue (issue_id),
  INDEX idx_wl_emp_date (emp_id, work_date)
) COMMENT '工时登记（研发费用按项目归集的依据）';

-- ========== 知识库 ==========
CREATE TABLE kb_space (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  space_key   VARCHAR(10)  NOT NULL UNIQUE,
  name        VARCHAR(50)  NOT NULL,
  description VARCHAR(200),
  owner       VARCHAR(50)
) COMMENT '知识空间';

CREATE TABLE kb_page (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  space_id      BIGINT       NOT NULL,
  parent_id     BIGINT,
  title         VARCHAR(200) NOT NULL,
  body          MEDIUMTEXT   NOT NULL COMMENT 'Markdown',
  version       INT          NOT NULL DEFAULT 1,
  status        VARCHAR(10)  NOT NULL COMMENT 'DRAFT/PUBLISHED/ARCHIVED',
  labels        VARCHAR(200),
  source_type   VARCHAR(10)  COMMENT '自动沉淀来源：8D/ECN/EAM/ROUTING/MANUAL',
  source_ref    VARCHAR(50),
  author_emp_id BIGINT,
  created_at    DATETIME     NOT NULL,
  updated_at    DATETIME     NOT NULL,
  view_count    INT          NOT NULL DEFAULT 0,
  INDEX idx_page_space (space_id, parent_id),
  INDEX idx_page_source (source_type, source_ref),
  FULLTEXT INDEX ft_page (title, body) WITH PARSER ngram
) COMMENT '知识页面（中文全文检索用 ngram 分词）';

CREATE TABLE kb_page_version (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  page_id       BIGINT       NOT NULL,
  version       INT          NOT NULL,
  title         VARCHAR(200) NOT NULL,
  body          MEDIUMTEXT   NOT NULL,
  author_emp_id BIGINT,
  saved_at      DATETIME     NOT NULL,
  change_note   VARCHAR(200),
  UNIQUE KEY uk_page_version (page_id, version)
) COMMENT '页面历史版本';

-- ========== 视图 ==========
CREATE VIEW v_pm_issue AS
SELECT i.id, i.issue_key, i.project_id, p.project_key, p.name AS project_name, i.type, i.title, i.status, i.resolution, i.priority,
       i.assignee_emp_id, a.name AS assignee_name, i.reporter_emp_id, r.name AS reporter_name, i.sprint_id, s.name AS sprint_name,
       i.epic_id, e.issue_key AS epic_key, e.title AS epic_title, i.story_points, i.estimate_hours, i.labels, i.ecn_id, ec.ecn_no,
       i.complaint_id, c.complaint_no, i.due_date, i.created_at, DATE(i.created_at) AS created_date, i.started_at, i.resolved_at,
       ROUND(TIMESTAMPDIFF(HOUR, i.started_at, i.resolved_at) / 24, 1) AS cycle_days,
       (SELECT COALESCE(SUM(w.hours), 0) FROM pm_worklog w WHERE w.issue_id = i.id) AS logged_hours
FROM pm_issue i JOIN pm_project p ON p.id = i.project_id
LEFT JOIN hr_employee a ON a.id = i.assignee_emp_id LEFT JOIN hr_employee r ON r.id = i.reporter_emp_id
LEFT JOIN pm_sprint s ON s.id = i.sprint_id LEFT JOIN pm_issue e ON e.id = i.epic_id
LEFT JOIN plm_ecn ec ON ec.id = i.ecn_id LEFT JOIN qms_complaint c ON c.id = i.complaint_id;

CREATE VIEW v_pm_project AS
SELECT p.*, l.name AS lead_name, np.project_no AS plm_project_no, np.stage AS plm_stage,
       (SELECT COUNT(*) FROM pm_issue i WHERE i.project_id = p.id AND i.status <> 'DONE') AS open_issues,
       (SELECT COUNT(*) FROM pm_issue i WHERE i.project_id = p.id AND i.type = 'BUG' AND i.status <> 'DONE') AS open_bugs,
       (SELECT COALESCE(SUM(w.hours), 0) FROM pm_worklog w JOIN pm_issue i ON i.id = w.issue_id WHERE i.project_id = p.id) AS logged_hours
FROM pm_project p JOIN hr_employee l ON l.id = p.lead_emp_id LEFT JOIN plm_project np ON np.id = p.plm_project_id;

CREATE VIEW v_pm_sprint AS
SELECT s.*, p.project_key, p.name AS project_name,
       (SELECT COUNT(*) FROM pm_issue i WHERE i.sprint_id = s.id) AS issue_count,
       (SELECT COALESCE(SUM(i.story_points), 0) FROM pm_issue i WHERE i.sprint_id = s.id AND i.status = 'DONE') AS done_points
FROM pm_sprint s JOIN pm_project p ON p.id = s.project_id;

CREATE VIEW v_pm_worklog AS
SELECT w.*, i.issue_key, i.title AS issue_title, i.type AS issue_type, p.project_key, p.name AS project_name, e.name AS emp_name
FROM pm_worklog w JOIN pm_issue i ON i.id = w.issue_id JOIN pm_project p ON p.id = i.project_id JOIN hr_employee e ON e.id = w.emp_id;

CREATE VIEW v_kb_page AS
SELECT pg.id, pg.space_id, s.space_key, s.name AS space_name, pg.parent_id, pg.title, pg.version, pg.status, pg.labels, pg.source_type,
       pg.source_ref, pg.author_emp_id, e.name AS author_name, pg.created_at, pg.updated_at, DATE(pg.updated_at) AS updated_date, pg.view_count,
       CHAR_LENGTH(pg.body) AS body_chars
FROM kb_page pg JOIN kb_space s ON s.id = pg.space_id LEFT JOIN hr_employee e ON e.id = pg.author_emp_id;
