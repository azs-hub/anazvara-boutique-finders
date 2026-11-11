CREATE TABLE `api_usage` (
	`provider` text NOT NULL,
	`day` text NOT NULL,
	`calls` integer DEFAULT 0 NOT NULL,
	`cost_usd` real DEFAULT 0 NOT NULL,
	PRIMARY KEY(`provider`, `day`)
);
--> statement-breakpoint
CREATE TABLE `businesses` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`city` text,
	`locality` text,
	`address` text,
	`lat` real,
	`lng` real,
	`website_domain` text,
	`instagram` text,
	`phone` text,
	`email` text,
	`maps_place_id` text,
	`status` text DEFAULT 'new' NOT NULL,
	`score` real,
	`first_seen_run` integer NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`first_seen_run`) REFERENCES `runs`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "businesses_status_check" CHECK("businesses"."status" in ('new', 'qualified', 'lead', 'review', 'rejected', 'contacted'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `businesses_maps_place_id_unique` ON `businesses` (`maps_place_id`);--> statement-breakpoint
CREATE INDEX `businesses_website_domain_idx` ON `businesses` (`website_domain`);--> statement-breakpoint
CREATE INDEX `businesses_instagram_idx` ON `businesses` (`instagram`);--> statement-breakpoint
CREATE INDEX `businesses_phone_idx` ON `businesses` (`phone`);--> statement-breakpoint
CREATE INDEX `businesses_city_status_idx` ON `businesses` (`city`,`status`);--> statement-breakpoint
CREATE TABLE `pages` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`url` text NOT NULL,
	`business_id` integer NOT NULL,
	`fetched_at` text NOT NULL,
	`status` integer,
	`text` text,
	`jsonld` text,
	FOREIGN KEY (`business_id`) REFERENCES `businesses`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `pages_business_id_url_unique` ON `pages` (`business_id`,`url`);--> statement-breakpoint
CREATE TABLE `qualifications` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`business_id` integer NOT NULL,
	`model` text NOT NULL,
	`json` text NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`business_id`) REFERENCES `businesses`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `qualifications_business_id_idx` ON `qualifications` (`business_id`);--> statement-breakpoint
CREATE TABLE `runs` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`city` text NOT NULL,
	`started_at` text NOT NULL,
	`finished_at` text,
	`params` text NOT NULL,
	`stats_json` text
);
--> statement-breakpoint
CREATE TABLE `sightings` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`business_id` integer,
	`source` text NOT NULL,
	`source_url` text,
	`query` text,
	`raw_json` text NOT NULL,
	FOREIGN KEY (`business_id`) REFERENCES `businesses`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "sightings_source_check" CHECK("sightings"."source" in ('osm', 'places', 'searxng', 'exa', 'tavily', 'guide'))
);
--> statement-breakpoint
CREATE INDEX `sightings_business_id_idx` ON `sightings` (`business_id`);--> statement-breakpoint
CREATE INDEX `sightings_unmerged_idx` ON `sightings` (`id`) WHERE "sightings"."business_id" is null;