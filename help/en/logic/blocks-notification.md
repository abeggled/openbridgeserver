---
title: "Blocks: Notification"
---

# Blocks: Notification

Blocks for sending notifications and for writing messages into a message archive.

## Notification {#logic-block-notify-message}

Sends a message via a configured MESSAGE adapter. Once an adapter is selected, the block shows
that adapter instance's active, configured **targets** as checkboxes — only the checked targets
receive the message. Title and message are fallback values: if the **Message** input is
connected, its value is sent instead of the fallback text. Without data point context in the
logic block, MESSAGE placeholders in the text are sent unchanged.

When a Pushover target is selected, the block offers the
[Pushover options](../adapters/list#adapters-message-pushover): image URL, URL, URL title,
priority (-2 to 2, with retry and expire for 2), sound, formatting, TTL and timestamp. The
additional **URL**, **URL title** and **Image URL** inputs override the static values, which
support the same variables as the title; inserted values are URL-encoded in URL and image URL. With HTML formatting, inserted variable values and a message arriving on the input are HTML-escaped for Pushover; HTML markup belongs in the fallback message. Other providers are unaffected. A stored priority 2 without retry and expire is sent as priority 1, as before, and noted in the log. **Sent** fires only
when every target was delivered successfully — an image that cannot be loaded fails the send.

**Title** and **fallback message** support variables: date/time (`###HH###`, `###DATE###`,
`###TS###` …) and objects assigned under **Variables** (`###OBS1###` …), list: [JSON Extractor](./blocks-integration#logic-block-json-extractor). The message
text connected via the input is not evaluated. If an `###OBSn###` slot is unconfigured or has no
value, nothing is sent and the error is shown on the block.

The block fires automatically as soon as a value arrives on the **Message** input, or the
**Trigger** input becomes true.

## Message Archive {#logic-block-message-archive}

Writes a message into a selected message archive. **Message type** and **Severity** control
how the message is categorized in the archive. Title and message are fallback values, used only
when the corresponding inputs (**Title**/**Message**) aren't connected — a connected input
overrides the fallback text.

The fallback texts support the same variables as the notification (date/time and `###OBSn###`,
see [JSON Extractor](./blocks-integration#logic-block-json-extractor)); with an unresolvable slot nothing is archived and the error is shown.

The block fires automatically as soon as a value arrives on the **Message** input, or the
**Trigger** input becomes true.
