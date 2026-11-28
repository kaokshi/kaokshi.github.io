// Single registry of Dzen channels collected from Dzen Studio.
// To connect a new channel: add it here and in config/zen_studio_channels.json (a test keeps
// them identical), add its statistics route to the DZEN_STUDIO_ROUTES secret and enroll the
// account that owns it once: node scripts/zen_studio_enroll.mjs enroll <key>.
// Each channel may live on its own Yandex account; a channel without its own session uses the
// primary channel's session (the case of several channels on one account).
export const STUDIO_CHANNEL_REGISTRY = Object.freeze([
 Object.freeze({key: "science", title: "За гранью очевидного"}),
 Object.freeze({key: "legends", title: "Легенды темных улиц"}),
 Object.freeze({key: "cinema", title: "Кадр за Кадром"}),
]);
export const STUDIO_CHANNELS = Object.freeze(STUDIO_CHANNEL_REGISTRY.map(channel => channel.key));
export const PRIMARY_CHANNEL = STUDIO_CHANNELS[0];
export const validChannel = value => typeof value === "string" && STUDIO_CHANNELS.includes(value);
export const channelTitle = key => STUDIO_CHANNEL_REGISTRY.find(channel => channel.key === key)?.title || null;
// Storage key of the encrypted session used for a channel; the primary keeps the original key.
export const vaultKey = channel => channel === PRIMARY_CHANNEL ? "vault" : "vault:" + channel;
