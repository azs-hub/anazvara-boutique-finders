/** A mistake in the user's input. The CLI prints the message alone and exits with code 1. */
export class UserError extends Error {
  override name = "UserError";
}
