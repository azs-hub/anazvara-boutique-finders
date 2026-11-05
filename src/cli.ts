import { Command } from "commander";

const program = new Command()
  .name("stockist")
  .description("Find physical boutiques in India that could stock Anazvara");

await program.parseAsync();
