CREATE TABLE `evidence` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`business_id` integer NOT NULL,
	`field` text NOT NULL,
	`value` text NOT NULL,
	`source` text NOT NULL,
	`source_url` text,
	`quote` text,
	`confidence` text,
	`sighting_id` integer,
	`page_id` integer,
	`run_id` integer,
	`created_at` text NOT NULL,
	FOREIGN KEY (`business_id`) REFERENCES `businesses`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`sighting_id`) REFERENCES `sightings`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`page_id`) REFERENCES `pages`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`run_id`) REFERENCES `runs`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "evidence_source_check" CHECK("source" in ('osm', 'places', 'searxng', 'exa', 'tavily', 'guide', 'website', 'llm', 'user', 'manual')),
	CONSTRAINT "evidence_confidence_check" CHECK("confidence" in ('high', 'medium', 'low'))
);
--> statement-breakpoint
CREATE INDEX `evidence_business_id_field_idx` ON `evidence` (`business_id`,`field`);--> statement-breakpoint
CREATE TABLE `jobs` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`type` text NOT NULL,
	`params` text NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`progress` text,
	`error` text,
	`run_id` integer,
	`created_at` text NOT NULL,
	`started_at` text,
	`finished_at` text,
	FOREIGN KEY (`run_id`) REFERENCES `runs`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "jobs_type_check" CHECK("type" in ('find', 'verify', 'qualify', 'export')),
	CONSTRAINT "jobs_status_check" CHECK("status" in ('queued', 'running', 'done', 'failed', 'cancelled'))
);
--> statement-breakpoint
CREATE INDEX `jobs_status_created_at_idx` ON `jobs` (`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `overrides` (
	`business_id` integer NOT NULL,
	`field` text NOT NULL,
	`value` text,
	`edited_at` text NOT NULL,
	`note` text,
	PRIMARY KEY(`business_id`, `field`),
	FOREIGN KEY (`business_id`) REFERENCES `businesses`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_businesses` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`country` text,
	`region` text,
	`city` text,
	`locality` text,
	`address` text,
	`lat` real,
	`lng` real,
	`store_type` text,
	`website_domain` text,
	`instagram` text,
	`phone` text,
	`whatsapp` text,
	`email` text,
	`contact_person` text,
	`contact_role` text,
	`maps_place_id` text,
	`maps_url` text,
	`maps_verified_at` text,
	`maps_match_confidence` text,
	`physical_store` text DEFAULT 'unknown' NOT NULL,
	`physical_store_confidence` text,
	`verified_at` text,
	`score` real,
	`fit` text,
	`priority` text,
	`system_decision` text,
	`crm_status` text,
	`last_contacted` text,
	`next_follow_up` text,
	`do_not_contact` integer DEFAULT false NOT NULL,
	`first_seen_run` integer NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`first_seen_run`) REFERENCES `runs`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "businesses_physical_store_check" CHECK("physical_store" in ('yes', 'no', 'unknown')),
	CONSTRAINT "businesses_physical_store_confidence_check" CHECK("physical_store_confidence" in ('high', 'medium', 'low')),
	CONSTRAINT "businesses_maps_match_confidence_check" CHECK("maps_match_confidence" in ('high', 'medium', 'low')),
	CONSTRAINT "businesses_system_decision_check" CHECK("system_decision" in ('lead', 'review', 'excluded')),
	CONSTRAINT "businesses_crm_status_check" CHECK("crm_status" in ('to_prospect', 'contacted', 'followed_up', 'qualified', 'in_discussion', 'offer_sent', 'to_recontact', 'lost', 'invalid')),
	CONSTRAINT "businesses_fit_check" CHECK("fit" in ('high', 'medium', 'low', 'exclude')),
	CONSTRAINT "businesses_priority_check" CHECK("priority" in ('top', 'high', 'medium', 'low', 'exclude'))
);
--> statement-breakpoint
-- Hand edit: drizzle-kit copied every new column from the old table. Only these columns exist there.
INSERT INTO `__new_businesses`("id", "name", "city", "locality", "address", "lat", "lng", "website_domain", "instagram", "phone", "email", "maps_place_id", "score", "first_seen_run", "updated_at") SELECT "id", "name", "city", "locality", "address", "lat", "lng", "website_domain", "instagram", "phone", "email", "maps_place_id", "score", "first_seen_run", "updated_at" FROM `businesses`;--> statement-breakpoint
DROP TABLE `businesses`;--> statement-breakpoint
ALTER TABLE `__new_businesses` RENAME TO `businesses`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE UNIQUE INDEX `businesses_maps_place_id_unique` ON `businesses` (`maps_place_id`);--> statement-breakpoint
CREATE INDEX `businesses_website_domain_idx` ON `businesses` (`website_domain`);--> statement-breakpoint
CREATE INDEX `businesses_instagram_idx` ON `businesses` (`instagram`);--> statement-breakpoint
CREATE INDEX `businesses_phone_idx` ON `businesses` (`phone`);--> statement-breakpoint
CREATE INDEX `businesses_city_idx` ON `businesses` (`city`);--> statement-breakpoint
CREATE INDEX `businesses_decision_priority_idx` ON `businesses` (`system_decision`,`priority`);--> statement-breakpoint
CREATE TABLE `__new_sightings` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`business_id` integer,
	`source` text NOT NULL,
	`source_url` text,
	`query` text,
	`raw_json` text NOT NULL,
	FOREIGN KEY (`business_id`) REFERENCES `businesses`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "sightings_source_check" CHECK("source" in ('osm', 'places', 'searxng', 'exa', 'tavily', 'guide', 'manual'))
);
--> statement-breakpoint
INSERT INTO `__new_sightings`("id", "business_id", "source", "source_url", "query", "raw_json") SELECT "id", "business_id", "source", "source_url", "query", "raw_json" FROM `sightings`;--> statement-breakpoint
DROP TABLE `sightings`;--> statement-breakpoint
ALTER TABLE `__new_sightings` RENAME TO `sightings`;--> statement-breakpoint
CREATE INDEX `sightings_business_id_idx` ON `sightings` (`business_id`);--> statement-breakpoint
CREATE INDEX `sightings_unmerged_idx` ON `sightings` (`id`) WHERE "sightings"."business_id" is null;--> statement-breakpoint
-- Hand edit: SQLite cannot add a NOT NULL column without a default, so add one, then backfill.
ALTER TABLE `runs` ADD `command` text DEFAULT '' NOT NULL;--> statement-breakpoint
UPDATE `runs` SET `command` = coalesce(json_extract(`params`, '$.command'), 'find');