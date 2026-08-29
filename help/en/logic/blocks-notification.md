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
logic block, MESSAGE placeholders in the text are sent unchanged. **Priority** ranges from -2
(very low) to 1 (high).

The block fires automatically as soon as a value arrives on the **Message** input, or the
**Trigger** input becomes true.

## Message Archive {#logic-block-message-archive}

Writes a message into a selected message archive. **Message type** and **Severity** control
how the message is categorized in the archive. Title and message are fallback values, used only
when the corresponding inputs (**Title**/**Message**) aren't connected — a connected input
overrides the fallback text.

The block fires automatically as soon as a value arrives on the **Message** input, or the
**Trigger** input becomes true.
