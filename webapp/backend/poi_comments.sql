/*
校园点位评论子系统 —— 表结构（MySQL）

背景：本仓库没有迁移体系（DDL 一律手写 .sql + 手工执行，见 users.sql）。
本文件供 DB 审阅与手工执行；`comments_store.py` 在首次访问时也会
执行同样内容的 `CREATE TABLE IF NOT EXISTS`，保证开箱即用（幂等）。

字符集统一 utf8mb4：评论正文可能包含 emoji（utf8 三字节存不下）。

状态机（poi_comments.status）
  visible 正常展示
  hidden  被举报达阈值自动折叠 / 管理员隐藏（数据保留，可恢复）
  deleted 软删除（仅管理员可见）
*/

SET NAMES utf8mb4;

-- ----------------------------
-- 评论主表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `poi_comments` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `poi_id` varchar(64) NOT NULL COMMENT '点位唯一标识，与 campus_pois.json 的 id 一致（如 plant_3023）',
  `poi_name` varchar(100) NOT NULL DEFAULT '' COMMENT '冗余点位名，列表页免回查 POI 库',
  `poi_category` varchar(32) NOT NULL DEFAULT '' COMMENT '冗余点位分类（plant/college/canteen/…）',
  `user_id` int(10) unsigned NOT NULL COMMENT '作者，对应 users.id',
  `username` varchar(20) NOT NULL COMMENT '冗余作者名，用户改名不影响历史展示',
  `content` varchar(500) NOT NULL,
  `content_len` smallint(5) unsigned NOT NULL DEFAULT '0' COMMENT '字符数（含标点，不含首尾空白）',
  `is_long` tinyint(1) NOT NULL DEFAULT '0' COMMENT '1=长评（弹窗预览优先级更高）',
  `status` varchar(16) NOT NULL DEFAULT 'visible' COMMENT 'visible / hidden / deleted',
  `like_count` int(10) unsigned NOT NULL DEFAULT '0',
  `love_count` int(10) unsigned NOT NULL DEFAULT '0',
  `report_count` int(10) unsigned NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_poi_created` (`poi_id`,`status`,`created_at`),
  KEY `idx_poi_hot` (`poi_id`,`status`,`like_count`),
  KEY `idx_user` (`user_id`),
  KEY `idx_status_report` (`status`,`report_count`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='校园点位用户留言';

-- ----------------------------
-- 表态表：点赞（like）与爱心（love）互相独立、可同时存在
-- ----------------------------
CREATE TABLE IF NOT EXISTS `comment_reactions` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `comment_id` bigint(20) unsigned NOT NULL,
  `user_id` int(10) unsigned NOT NULL,
  `reaction` varchar(8) NOT NULL COMMENT 'like / love',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_comment_user_reaction` (`comment_id`,`user_id`,`reaction`),
  KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评论表态：点赞/爱心';

-- ----------------------------
-- 举报表：同一用户对同一评论只能举报一次（防刷阈值）
-- ----------------------------
CREATE TABLE IF NOT EXISTS `comment_reports` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `comment_id` bigint(20) unsigned NOT NULL,
  `user_id` int(10) unsigned NOT NULL,
  `reason_code` varchar(32) NOT NULL COMMENT 'spam/porn/abuse/misinfo/privacy/politics/other',
  `reason_text` varchar(200) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_comment_user` (`comment_id`,`user_id`),
  KEY `idx_comment` (`comment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评论举报记录';

-- ----------------------------
-- 管理操作留痕：谁在什么时候对哪条评论做了什么
-- ----------------------------
CREATE TABLE IF NOT EXISTS `comment_moderation_log` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `comment_id` bigint(20) unsigned NOT NULL,
  `admin_action` varchar(32) NOT NULL COMMENT 'auto_hide/restore/delete/ban_author',
  `note` varchar(200) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_comment` (`comment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评论管理操作台账';

-- ----------------------------
-- 封禁名单：被管理员封禁的作者不得再发布留言
-- ----------------------------
CREATE TABLE IF NOT EXISTS `comment_banned_users` (
  `user_id` int(10) unsigned NOT NULL,
  `username` varchar(20) NOT NULL DEFAULT '',
  `note` varchar(200) NOT NULL DEFAULT '',
  `banned_at` datetime NOT NULL,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评论封禁用户名单';
