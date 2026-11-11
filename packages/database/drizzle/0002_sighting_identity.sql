ALTER TABLE `sightings` ADD `external_id` text NOT NULL;--> statement-breakpoint
ALTER TABLE `sightings` ADD `place_key` text NOT NULL;--> statement-breakpoint
ALTER TABLE `sightings` ADD `run_id` integer NOT NULL REFERENCES runs(id);--> statement-breakpoint
ALTER TABLE `sightings` ADD `name` text;--> statement-breakpoint
ALTER TABLE `sightings` ADD `address` text;--> statement-breakpoint
ALTER TABLE `sightings` ADD `lat` real;--> statement-breakpoint
ALTER TABLE `sightings` ADD `lng` real;--> statement-breakpoint
ALTER TABLE `sightings` ADD `phone` text;--> statement-breakpoint
ALTER TABLE `sightings` ADD `website` text;--> statement-breakpoint
ALTER TABLE `sightings` ADD `instagram` text;--> statement-breakpoint
CREATE UNIQUE INDEX `sightings_source_external_id_place_key_unique` ON `sightings` (`source`,`external_id`,`place_key`);