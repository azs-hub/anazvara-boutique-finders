import { config as loadDotenv } from "dotenv";
import { createProgram, describeError } from "./cli/program.js";

// Values already set in the shell win over .env.
loadDotenv({ quiet: true });

try {
  await createProgram({ env: process.env, out: (line) => console.log(line) }).parseAsync();
} catch (error) {
  const { message, exitCode } = describeError(error);
  console.error(message);
  process.exitCode = exitCode;
}
