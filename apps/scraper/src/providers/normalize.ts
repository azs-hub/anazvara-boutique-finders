// Instagram paths that are not profiles.
const NOT_PROFILES = new Set(["p", "reel", "reels", "explore", "stories", "tv", "accounts", "direct", "about"]);
const INSTAGRAM_URL = /^(?:https?:\/\/)?(?:www\.|m\.)?(?:instagram\.com|instagr\.am)\/([^/?#]+)/i;
const HANDLE = /^[a-z0-9._]{1,30}$/;

/**
 * An Instagram handle, lowercase and without "@", from a handle or a profile
 * URL. Undefined when the value is empty, another site, or not a profile.
 */
export function instagramHandle(value: string | undefined): string | undefined {
  const text = value?.trim();
  if (!text) return undefined;

  const fromUrl = INSTAGRAM_URL.exec(text)?.[1];
  // Anything else that looks like a URL is not a handle.
  if (fromUrl === undefined && /[/:]/.test(text)) return undefined;

  const handle = (fromUrl ?? text).replace(/^@/, "").toLowerCase();
  return HANDLE.test(handle) && !NOT_PROFILES.has(handle) ? handle : undefined;
}

/** Trims a text value; empty becomes undefined. */
export function clean(value: string | undefined): string | undefined {
  const text = value?.trim();
  return text ? text : undefined;
}
