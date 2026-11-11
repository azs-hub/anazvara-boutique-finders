import { Command, Option } from "commander";
import { ConfigError, PLACE_TYPES, PROVIDER_NAMES, type PlaceType } from "@anazvara/shared";
import {
  DEFAULT_NEW_COUNT,
  runBudget,
  runConfig,
  runFind,
  runMigrate,
  runPlaces,
  type CliContext,
  type FindOptions,
} from "./commands.js";
import { UserError } from "./errors.js";

export function createProgram(ctx: CliContext): Command {
  const program = new Command()
    .name("stockist")
    .description("Find physical boutiques that could stock Anazvara")
    .configureOutput({ writeOut: (text) => ctx.out(text.trimEnd()) })
    .showHelpAfterError();

  program
    .command("find")
    .description("Find boutiques in a city, region or country")
    .requiredOption("--location <place>", "place key, name or alias from config/places.json")
    .option("--new <n>", "how many new boutiques to find", String(DEFAULT_NEW_COUNT))
    .option("--sources <list>", `comma-separated providers (${PROVIDER_NAMES.join(", ")}); default all`)
    .action((options: FindOptions) => runFind(ctx, options));

  program
    .command("places")
    .description("List the places in config/places.json")
    .addOption(new Option("--type <type>", "only places of this type").choices(PLACE_TYPES))
    .action((options: { type?: PlaceType }) => runPlaces(ctx, options));

  program
    .command("config")
    .description("Validate the config files and show providers and the database path")
    .action(() => runConfig(ctx));

  program
    .command("budget")
    .description("Show API usage against daily and monthly limits")
    .action(() => runBudget(ctx));

  program
    .command("db")
    .description("Database commands")
    .command("migrate")
    .description("Create the database and apply migrations")
    .action(() => runMigrate(ctx));

  return program;
}

/**
 * Turns an error into what the user sees and the exit code. Input and config
 * mistakes print their message only; anything else is a bug and keeps its stack.
 */
export function describeError(error: unknown): { message: string; exitCode: number } {
  if (error instanceof UserError || error instanceof ConfigError) {
    return { message: `error: ${error.message}`, exitCode: 1 };
  }
  return { message: error instanceof Error ? (error.stack ?? String(error)) : String(error), exitCode: 1 };
}
