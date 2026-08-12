const state = {
  settings: null,
  chats: [],
  voices: [],
  models: [],
  activeChat: null,
  generating: false,
  audioQueue: [],
  audio: null,
  forcePlayback: false,
  wikiPoll: null,
  audioCtx: null,
  messageSoundCatalog: {
    incoming: [
      { id: "incoming1", label: "Incoming 1", file: "incoming1.mp3" },
      { id: "incoming2", label: "Incoming 2", file: "incoming2.mp3" },
      { id: "incoming3", label: "Incoming 3", file: "incoming3.mp3" },
    ],
    outgoing: [
      { id: "outgoing1", label: "Outgoing 1", file: "outgoing1.mp3" },
    ],
  },
  sfxPlayer: null,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  chatList: $("#chat-list"),
  chatCount: $("#chat-count"),
  activeTitle: $("#active-title"),
  messages: $("#messages"),
  welcome: $("#welcome"),
  input: $("#message-input"),
  form: $("#composer-form"),
  send: $("#send-button"),
  stop: $("#stop-button"),
  generation: $("#generation-status"),
  generationLabel: $("#generation-label"),
  settingsDrawer: $("#settings-drawer"),
  settingsBackdrop: $("#drawer-backdrop"),
  settingsForm: $("#settings-form"),
  modelChip: $("#model-chip-text"),
  voiceBadge: $("#voice-badge"),
  serverStatus: $("#server-status"),
  serverDetail: $("#server-detail"),
  statusOrb: $("#status-orb"),
  sidebar: $("#sidebar"),
  endChat: $("#end-chat"),
  resumeChat: $("#resume-chat"),
  wikiSync: $("#wiki-sync"),
  statusPill: $("#status-pill"),
  statusPillLabel: $("#status-pill-label"),
  wikiStatusLine: $("#wiki-status-line"),
  lifecycleBanner: $("#lifecycle-banner"),
  lifecycleBannerTitle: $("#lifecycle-banner-title"),
  lifecycleBannerDetail: $("#lifecycle-banner-detail"),
  lifecycleBannerAction: $("#lifecycle-banner-action"),
  chatWikiToggleWrap: $("#chat-wiki-toggle-wrap"),
  chatWikiToggle: $("#chat-wiki-toggle"),
  testVault: $("#test-vault"),
  browseVault: $("#browse-vault"),
  wikiTestResult: $("#wiki-test-result"),
  volumeSlider: $("#message_sound_volume"),
  volumeLabel: $("#message-sound-volume-label"),
  previewIncoming: $("#preview-incoming"),
  previewOutgoing: $("#preview-outgoing"),
};

/* ---- Message notification sounds (MP3s from /sounds) ---- */

function soundFileFor(kind, soundId) {
  const list = kind === "outgoing" || kind === "sent" || kind === "end"
    ? state.messageSoundCatalog.outgoing
    : state.messageSoundCatalog.incoming;
  const id = soundId
    || (kind === "outgoing" || kind === "sent" || kind === "end"
      ? state.settings?.message_sound_outgoing
      : state.settings?.message_sound_incoming);
  const hit = list.find((item) => item.id === id) || list[0];
  return hit ? `/sounds/${hit.file}` : null;
}

function soundVolume() {
  const raw = Number(state.settings?.message_sound_volume);
  if (!Number.isFinite(raw)) return 0.7;
  return Math.max(0, Math.min(1, raw));
}

function soundsEnabled() {
  if (state.settings?.message_sounds === false) return false;
  if (state.settings?.message_sounds_muted === true) return false;
  return true;
}

function playMessageSound(kind, { force = false, soundId = null } = {}) {
  if (!force && !soundsEnabled()) return;
  const group = (kind === "sent" || kind === "outgoing" || kind === "end") ? "outgoing" : "incoming";
  const url = soundFileFor(group, soundId);
  if (!url) return;
  try {
    if (state.sfxPlayer) {
      state.sfxPlayer.pause();
      state.sfxPlayer = null;
    }
    const audio = new Audio(url);
    audio.volume = soundVolume();
    state.sfxPlayer = audio;
    audio.play().catch(() => {
      /* autoplay policy — first user gesture unlocks later plays */
    });
  } catch (_) { /* ignore */ }
}

function populateSoundSelects() {
  const form = elements.settingsForm?.elements;
  if (!form) return;
  const incoming = form.namedItem("message_sound_incoming");
  const outgoing = form.namedItem("message_sound_outgoing");
  if (incoming) {
    incoming.innerHTML = state.messageSoundCatalog.incoming
      .map((item) => `<option value="${escapeHTML(item.id)}">${escapeHTML(item.label)}</option>`)
      .join("");
    incoming.value = state.settings?.message_sound_incoming || state.messageSoundCatalog.incoming[0]?.id || "";
  }
  if (outgoing) {
    outgoing.innerHTML = state.messageSoundCatalog.outgoing
      .map((item) => `<option value="${escapeHTML(item.id)}">${escapeHTML(item.label)}</option>`)
      .join("");
    outgoing.value = state.settings?.message_sound_outgoing || state.messageSoundCatalog.outgoing[0]?.id || "";
  }
}

function updateVolumeLabel() {
  if (!elements.volumeLabel || !elements.volumeSlider) return;
  const value = Number(elements.volumeSlider.value);
  elements.volumeLabel.textContent = `${Math.round((Number.isFinite(value) ? value : 0.7) * 100)}%`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  if (response.status === 204) return null;
  return response.json();
}

function escapeHTML(value = "") {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function renderMarkdown(source = "") {
  const codeBlocks = [];
  let text = source.replace(/```([^\n]*)\n?([\s\S]*?)```/g, (_, language, code) => {
    const token = `\u0000CODE${codeBlocks.length}\u0000`;
    codeBlocks.push(`<pre><code data-language="${escapeHTML(language.trim())}">${escapeHTML(code.trim())}</code></pre>`);
    return `\n\n${token}\n\n`;
  });
  text = escapeHTML(text)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  return text.split(/\n{2,}/).map((block) => {
    const trimmed = block.trim();
    if (!trimmed) return "";
    const code = trimmed.match(/^\u0000CODE(\d+)\u0000$/);
    if (code) return codeBlocks[Number(code[1])];
    if (/^(?:[-*+] .+(?:\n|$))+/.test(trimmed)) {
      const items = trimmed.split("\n").map((line) => line.replace(/^[-*+]\s+/, "")).filter(Boolean);
      return `<ul>${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) return `<h${heading[1].length}>${heading[2]}</h${heading[1].length}>`;
    return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

function toast(message, type = "") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  $("#toast-region").append(node);
  setTimeout(() => node.remove(), 4800);
}

function formatTime(value) {
  if (!value) return "now";
  const date = new Date(value);
  const delta = Date.now() - date.getTime();
  if (delta < 60_000) return "now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function initials(title = "") {
  const parts = title.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "O";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function chatStatusChip(chat) {
  const wiki = chat.wiki_status || chat.wiki?.last_status;
  if (wiki === "running" || wiki === "queued") {
    return `<span class="chat-chip wiki-running">Wiki…</span>`;
  }
  if (wiki === "error") return `<span class="chat-chip wiki-error">Wiki err</span>`;
  if (chat.status === "ended") return `<span class="chat-chip ended">Ended</span>`;
  return `<span class="chat-chip open">Open</span>`;
}

function renderChatList() {
  elements.chatCount.textContent = state.chats.length;
  elements.chatList.innerHTML = "";
  state.chats.forEach((chat) => {
    const item = document.createElement("div");
    item.className = `chat-item ${state.activeChat?.id === chat.id ? "active" : ""}`;
    item.tabIndex = 0;
    const preview = chat.preview || `${chat.message_count || 0} messages`;
    item.innerHTML = `
      <div class="chat-avatar" aria-hidden="true">${escapeHTML(initials(chat.title))}</div>
      <div class="chat-item-body">
        <div class="chat-item-top">
          <div class="chat-title">${escapeHTML(chat.title)}</div>
          <span class="chat-time">${formatTime(chat.updated_at)}</span>
        </div>
        <div class="chat-preview">${escapeHTML(preview)}</div>
      </div>
      <div class="chat-item-meta">
        ${chatStatusChip(chat)}
        <button class="chat-menu" aria-label="Conversation options">•••</button>
      </div>`;
    item.addEventListener("click", (event) => {
      if (!event.target.closest(".chat-menu")) loadChat(chat.id);
    });
    item.addEventListener("keydown", (event) => { if (event.key === "Enter") loadChat(chat.id); });
    item.querySelector(".chat-menu").addEventListener("click", (event) => showChatMenu(event, chat));
    elements.chatList.append(item);
  });
}

async function showChatMenu(event, chat) {
  event.stopPropagation();
  const action = prompt(`Type "rename" or "delete" for “${chat.title}”.`, "rename");
  if (action === "rename") {
    const title = prompt("Conversation name", chat.title);
    if (title?.trim()) {
      await api(`/api/chats/${chat.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
      await refreshChats();
      if (state.activeChat?.id === chat.id) { state.activeChat.title = title.trim(); updateHeader(); }
    }
  } else if (action === "delete" && confirm(`Delete “${chat.title}”? This cannot be undone. Vault pages are kept.`)) {
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    if (state.activeChat?.id === chat.id) state.activeChat = null;
    await refreshChats();
    if (state.chats.length) await loadChat(state.chats[0].id); else await createChat();
  }
}

function messageNode(message) {
  if (message.role === "system" || message.kind === "tool") {
    const row = document.createElement("div");
    row.className = "message-row system";
    row.dataset.messageId = message.id;
    row.innerHTML = `<div class="tool-chip ${message.active ? "active" : ""}">${escapeHTML(message.content || "")}</div>`;
    return row;
  }

  const row = document.createElement("div");
  row.className = `message-row ${message.role}`;
  row.dataset.messageId = message.id;
  const assistant = message.role === "assistant";
  const status = message.status === "streaming" ? '<span class="cursor"></span>' : "";
  const body = assistant
    ? renderMarkdown(message.content || "")
    : `<p>${escapeHTML(message.content || "").replace(/\n/g, "<br>")}</p>`;
  row.innerHTML = `
    <div class="bubble ${message.role} ${message.status === "error" ? "error" : ""}">
      <div class="bubble-content">${body}${status}</div>
      ${assistant ? `<div class="message-actions"><button class="replay-button" ${!message.content ? "disabled" : ""}>▶ Speak</button><span class="speech-status"></span></div>` : ""}
      <div class="bubble-meta"><span>${formatTime(message.created_at)}</span></div>
    </div>`;
  if (assistant) {
    row.querySelector(".replay-button").addEventListener("click", () => replayMessage(message.id));
  }
  return row;
}

function renderMessages() {
  elements.messages.innerHTML = "";
  const messages = state.activeChat?.messages || [];
  if (!messages.length) {
    elements.messages.innerHTML = $("#welcome")?.outerHTML || `<div class="welcome"><p class="eyebrow">VOICE-LINK READY</p><h2>Speak with your local intelligence.</h2><p>Your conversation begins here.</p></div>`;
  } else {
    messages.forEach((message) => elements.messages.append(messageNode(message)));
  }
  scrollBottom();
}

function scrollBottom() {
  requestAnimationFrame(() => { elements.messages.scrollTop = elements.messages.scrollHeight; });
}

function findMessage(id) {
  return state.activeChat?.messages?.find((message) => message.id === id);
}

function updateMessage(id) {
  const message = findMessage(id);
  const oldNode = elements.messages.querySelector(`[data-message-id="${CSS.escape(id)}"]`);
  if (!message || !oldNode) return;
  oldNode.replaceWith(messageNode(message));
  scrollBottom();
}

function updateSpeechStatus(messageId, label, active = false) {
  const node = elements.messages.querySelector(`[data-message-id="${CSS.escape(messageId)}"] .speech-status`);
  if (node) { node.textContent = label; node.classList.toggle("active", active); }
}

function appendToolChip(text, active = false) {
  const id = `tool-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const fake = { id, role: "system", kind: "tool", content: text, active, created_at: new Date().toISOString() };
  elements.messages.append(messageNode(fake));
  scrollBottom();
  return id;
}

function isEnded() {
  return state.activeChat?.status === "ended";
}

function updateLifecycleBanner() {
  const banner = elements.lifecycleBanner;
  if (!banner) return;
  const chat = state.activeChat;
  if (!chat) {
    banner.hidden = true;
    return;
  }
  const ended = chat.status === "ended";
  const hasMessages = Boolean(chat.messages?.length);
  // Show status strip whenever a conversation is loaded; action only when useful
  banner.hidden = false;
  banner.classList.toggle("ended", ended);
  banner.classList.toggle("open", !ended);
  const wikiOn = state.settings?.wiki_enabled && chat.wiki_enabled !== false;
  if (ended) {
    elements.lifecycleBannerTitle.textContent = "Conversation ended";
    elements.lifecycleBannerDetail.textContent = wikiOn
      ? "Resume to keep chatting, or sync the wiki from the header."
      : "Resume to keep chatting. Wiki is off for this conversation.";
    elements.lifecycleBannerAction.textContent = "Resume";
    elements.lifecycleBannerAction.hidden = false;
    elements.lifecycleBannerAction.disabled = state.generating;
  } else {
    elements.lifecycleBannerTitle.textContent = "Conversation open";
    elements.lifecycleBannerDetail.textContent = hasMessages
      ? (wikiOn
        ? "Keep chatting. End to archive and write the wiki (if enabled)."
        : "Keep chatting. Wiki is off for this chat — End will not write to the vault.")
      : "Send a message to start. End appears after the first exchange.";
    elements.lifecycleBannerAction.textContent = "End";
    elements.lifecycleBannerAction.hidden = !hasMessages || state.generating;
    elements.lifecycleBannerAction.disabled = state.generating || !hasMessages;
  }
}

function updateHeader() {
  elements.activeTitle.textContent = state.activeChat?.title || "New conversation";
  const modelId = state.activeChat?.model_id || state.settings?.chat_model;
  const model = state.models.find((item) => item.id === modelId);
  elements.modelChip.textContent = model?.name || modelId || "No model selected";
  elements.voiceBadge.textContent = `${(state.settings?.voice || "tara").toUpperCase()} VOICE`;

  const ended = isEnded();
  const hasMessages = Boolean(state.activeChat?.messages?.length);
  elements.statusPill.classList.toggle("ended", ended);
  elements.statusPillLabel.textContent = ended ? "ENDED" : "OPEN";
  elements.endChat.hidden = ended || !hasMessages || state.generating;
  elements.resumeChat.hidden = !ended;
  const globalWiki = Boolean(state.settings?.wiki_enabled);
  const chatWiki = state.activeChat?.wiki_enabled !== false;
  if (elements.chatWikiToggleWrap) {
    elements.chatWikiToggleWrap.hidden = !globalWiki;
    elements.chatWikiToggleWrap.classList.toggle("off", !chatWiki);
  }
  if (elements.chatWikiToggle) {
    elements.chatWikiToggle.checked = chatWiki;
    elements.chatWikiToggle.disabled = !globalWiki || state.generating;
  }
  elements.wikiSync.hidden = !globalWiki || !chatWiki || !hasMessages;
  elements.form.hidden = ended;
  elements.input.disabled = ended || state.generating;
  elements.send.disabled = ended || state.generating;
  updateLifecycleBanner();

  const wiki = state.activeChat?.wiki || {};
  const status = wiki.last_status || "idle";
  if (status === "running" || status === "queued") {
    elements.wikiStatusLine.hidden = false;
    elements.wikiStatusLine.className = "wiki-status-line active";
    elements.wikiStatusLine.textContent = status === "queued"
      ? "Wiki scribe queued (reuses your chat model)…"
      : "Writing conversation to Obsidian wiki…";
  } else if (status === "ok" && wiki.last_synced_at) {
    elements.wikiStatusLine.hidden = false;
    elements.wikiStatusLine.className = "wiki-status-line";
    const pages = (wiki.pages_touched || []).length;
    elements.wikiStatusLine.textContent = pages
      ? `Wiki updated · ${pages} page${pages === 1 ? "" : "s"}`
      : `Wiki updated ${formatTime(wiki.last_synced_at)}`;
  } else if (status === "error") {
    elements.wikiStatusLine.hidden = false;
    elements.wikiStatusLine.className = "wiki-status-line error";
    elements.wikiStatusLine.textContent = wiki.last_error || "Wiki scribe failed";
  } else {
    elements.wikiStatusLine.hidden = true;
  }

  applyChatLocks();
  maybePollWiki();
}

function maybePollWiki() {
  if (state.wikiPoll) {
    clearInterval(state.wikiPoll);
    state.wikiPoll = null;
  }
  const status = state.activeChat?.wiki?.last_status;
  if (!state.activeChat || (status !== "running" && status !== "queued")) return;
  state.wikiPoll = setInterval(async () => {
    if (!state.activeChat) return;
    try {
      const chat = await api(`/api/chats/${state.activeChat.id}`);
      state.activeChat.wiki = chat.wiki;
      state.activeChat.status = chat.status;
      const listItem = state.chats.find((item) => item.id === chat.id);
      if (listItem) {
        listItem.wiki_status = chat.wiki?.last_status;
        listItem.status = chat.status;
      }
      renderChatList();
      updateHeader();
      if (chat.wiki?.last_status === "ok") toast("Wiki updated from this conversation.");
      if (chat.wiki?.last_status === "error") toast(chat.wiki.last_error || "Wiki sync failed", "error");
      if (chat.wiki?.last_status !== "running" && chat.wiki?.last_status !== "queued") {
        clearInterval(state.wikiPoll);
        state.wikiPoll = null;
      }
    } catch (_) { /* ignore transient poll errors */ }
  }, 1500);
}

async function refreshChats() {
  state.chats = await api("/api/chats");
  renderChatList();
}

async function createChat() {
  if (state.generating) return toast("Stop the current response before starting a new chat.", "error");
  const chat = await api("/api/chats", { method: "POST", body: JSON.stringify({}) });
  await refreshChats();
  await loadChat(chat.id);
}

async function loadChat(id) {
  if (state.generating && state.activeChat?.id !== id) return toast("Stop the current response before switching chats.", "error");
  state.activeChat = await api(`/api/chats/${id}`);
  if (!state.activeChat.messages.length && !state.activeChat.model_id && state.settings?.chat_model) {
    state.activeChat = await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ model_id: state.settings.chat_model, system_prompt: state.settings.system_prompt }),
    });
  }
  renderChatList();
  renderMessages();
  updateHeader();
  elements.sidebar.classList.remove("open");
}

async function endConversation() {
  if (!state.activeChat || state.generating) return;
  if (!state.activeChat.messages?.length) return toast("Nothing to end yet.", "error");
  if (!confirm("End this conversation? If the wiki is enabled, the scribe will write pages using your chat model.")) return;
  try {
    state.activeChat = await api(`/api/chats/${state.activeChat.id}/end`, { method: "POST", body: "{}" });
    playMessageSound("end");
    await refreshChats();
    updateHeader();
    if (state.settings?.wiki_enabled && state.settings?.wiki_auto_on_end) {
      toast("Conversation ended. Writing to wiki…");
    } else {
      toast("Conversation ended.");
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

async function resumeConversation() {
  if (!state.activeChat) return;
  try {
    state.activeChat = await api(`/api/chats/${state.activeChat.id}/resume`, { method: "POST", body: "{}" });
    playMessageSound("resume");
    await refreshChats();
    updateHeader();
    elements.input.focus();
    toast("Conversation resumed.");
  } catch (error) {
    toast(error.message, "error");
  }
}

function onLifecycleBannerAction() {
  if (isEnded()) resumeConversation();
  else endConversation();
}

async function syncWiki() {
  if (!state.activeChat) return;
  try {
    state.activeChat = await api(`/api/chats/${state.activeChat.id}/wiki-sync`, { method: "POST", body: "{}" });
    updateHeader();
    toast("Wiki sync started (same chat model)…");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function setChatWikiEnabled(enabled) {
  if (!state.activeChat) return;
  try {
    state.activeChat = await api(`/api/chats/${state.activeChat.id}`, {
      method: "PATCH",
      body: JSON.stringify({ wiki_enabled: enabled }),
    });
    const listItem = state.chats.find((item) => item.id === state.activeChat.id);
    if (listItem) listItem.wiki_enabled = enabled;
    renderChatList();
    updateHeader();
    toast(enabled ? "Wiki enabled for this chat." : "Wiki disabled for this chat.");
  } catch (error) {
    if (elements.chatWikiToggle) elements.chatWikiToggle.checked = !enabled;
    toast(error.message, "error");
  }
}

function setGenerating(active, label = "Thinking locally…") {
  state.generating = active;
  elements.generation.hidden = !active;
  elements.generationLabel.textContent = label;
  elements.stop.hidden = !active;
  elements.send.hidden = active;
  elements.input.disabled = active || isEnded();
  updateHeader();
}

async function consumeSSE(response, handler) {
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, "\n");
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let eventName = "message";
      const data = [];
      block.split("\n").forEach((line) => {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trim());
      });
      if (data.length) {
        let payload = data.join("\n");
        try { payload = JSON.parse(payload); } catch (_) {}
        await handler(eventName, payload);
      }
    }
    if (done) break;
  }
}

async function handleStreamEvent(event, payload) {
  if (event === "message_started") {
    const existingWelcome = elements.messages.querySelector(".welcome");
    if (existingWelcome) elements.messages.innerHTML = "";
    state.activeChat.messages.push(payload.user, payload.assistant);
    elements.messages.append(messageNode(payload.user), messageNode(payload.assistant));
    scrollBottom();
    playMessageSound("sent");
  } else if (event === "assistant_delta") {
    const message = findMessage(payload.message_id);
    if (message) { message.content += payload.content; updateMessage(message.id); }
  } else if (event === "tool_started") {
    setGenerating(true, `Wiki tool: ${payload.name || "reading"}…`);
    appendToolChip(`Using wiki · ${payload.name || "tool"}`, true);
  } else if (event === "tool_done") {
    setGenerating(true, "Thinking locally…");
    if (payload.summary) appendToolChip(payload.summary, false);
  } else if (event === "assistant_done") {
    const incoming = payload.message;
    const index = state.activeChat.messages.findIndex((item) => item.id === incoming.id);
    if (index >= 0) state.activeChat.messages[index] = incoming;
    if (payload.chat?.title) state.activeChat.title = payload.chat.title;
    if (payload.chat?.status) state.activeChat.status = payload.chat.status;
    updateMessage(incoming.id);
    if (incoming.status === "complete" && incoming.content) {
      playMessageSound("received");
    }
    updateHeader();
    await refreshChats();
  } else if (event === "tts_started") {
    const n = payload.chunks || 1;
    setGenerating(
      true,
      n > 1
        ? `Orpheus is synthesizing ${n} parts into one continuous clip…`
        : "Orpheus is shaping continuous speech…"
    );
    updateSpeechStatus(payload.message_id, "Synthesizing voice…", true);
  } else if (event === "tts_progress") {
    const total = payload.total || 1;
    const index = (payload.index ?? 0) + 1;
    setGenerating(true, `Orpheus speech ${index}/${total}…`);
    updateSpeechStatus(payload.message_id, `Building continuous audio ${index}/${total}`, true);
  } else if (event === "audio_ready") {
    updateSpeechStatus(
      payload.message_id,
      payload.continuous ? "Continuous voice ready" : `Voice segment ${payload.index + 1} ready`,
      true
    );
    enqueueAudio(payload.url);
  } else if (event === "tts_done") {
    updateSpeechStatus(payload.message_id, payload.cancelled ? "Speech stopped" : "Voice ready");
  } else if (event === "error") {
    toast(payload.message || "Generation failed", "error");
    if (payload.message_id) updateSpeechStatus(payload.message_id, payload.stage === "tts" ? "Speech unavailable" : "Response interrupted");
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const content = elements.input.value.trim();
  if (!content || state.generating || !state.activeChat) return;
  if (isEnded()) return toast("Resume the conversation before sending messages.", "error");
  if (!state.activeChat.model_id) return toast("Choose a chat model in Settings first.", "error");
  elements.input.value = "";
  resizeInput();
  setGenerating(true);
  try {
    const response = await fetch(`/api/chats/${state.activeChat.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    await consumeSSE(response, handleStreamEvent);
  } catch (error) {
    toast(error.message, "error");
    await loadChat(state.activeChat.id);
  } finally {
    setGenerating(false);
    elements.input.focus();
  }
}

async function stopGeneration() {
  if (!state.activeChat) return;
  await api(`/api/chats/${state.activeChat.id}/cancel`, { method: "POST", body: "{}" });
  state.audioQueue = [];
  if (state.audio) { state.audio.pause(); state.audio = null; }
  elements.generationLabel.textContent = "Stopping…";
}

async function replayMessage(messageId) {
  if (state.generating) return toast("Wait for the current generation to finish.", "error");
  state.audioQueue = [];
  if (state.audio) { state.audio.pause(); state.audio = null; }
  setGenerating(true, "Preparing voice replay…");
  state.forcePlayback = true;
  try {
    const response = await fetch(`/api/chats/${state.activeChat.id}/messages/${messageId}/speech`, { method: "POST" });
    await consumeSSE(response, handleStreamEvent);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.forcePlayback = false;
    setGenerating(false);
  }
}

function enqueueAudio(url) {
  state.audioQueue.push(url);
  if ((state.settings.autoplay || state.forcePlayback) && !state.audio) playNextAudio();
}

function playNextAudio() {
  const url = state.audioQueue.shift();
  if (!url) { state.audio = null; return; }
  const audio = new Audio(url);
  state.audio = audio;
  audio.addEventListener("ended", playNextAudio, { once: true });
  audio.addEventListener("error", () => { toast("A voice segment could not be played.", "error"); playNextAudio(); }, { once: true });
  audio.play().catch(() => {
    state.audio = null;
    state.audioQueue.unshift(url);
    toast("Your browser blocked autoplay. Press Speak to allow audio.");
  });
}

function isChatUnlimited() {
  const value = Number(state.settings?.chat_max_tokens);
  return Number.isFinite(value) && value < 0;
}

function isTtsUnlimited() {
  const value = Number(state.settings?.tts_max_tokens);
  return Number.isFinite(value) && value < 0;
}

function syncTokenLimitUI(checkboxId, fieldName, fallback) {
  const unlimited = $(checkboxId);
  const field = elements.settingsForm?.elements?.namedItem(fieldName);
  if (!unlimited || !field) return;
  const on = unlimited.checked;
  field.disabled = on;
  field.placeholder = on ? "Unlimited" : "";
  if (on) {
    field.dataset.previousValue = field.value || field.dataset.previousValue || String(fallback);
    field.value = "";
  } else if (!field.value) {
    field.value = field.dataset.previousValue || String(fallback);
  }
}

function syncChatTokenLimitUI() {
  syncTokenLimitUI("#chat_tokens_unlimited", "chat_max_tokens", 1200);
}

function syncTtsTokenLimitUI() {
  syncTokenLimitUI("#tts_tokens_unlimited", "tts_max_tokens", 1200);
}

function populateSettings() {
  const form = elements.settingsForm.elements;
  Object.entries(state.settings).forEach(([key, value]) => {
    const field = form.namedItem(key);
    if (!field) return;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = value ?? "";
  });
  const unlimited = $("#chat_tokens_unlimited");
  if (unlimited) unlimited.checked = isChatUnlimited();
  const tokenField = form.namedItem("chat_max_tokens");
  if (tokenField && isChatUnlimited()) {
    tokenField.dataset.previousValue = "1200";
    tokenField.value = "";
  }
  syncChatTokenLimitUI();
  const ttsUnlimited = $("#tts_tokens_unlimited");
  if (ttsUnlimited) ttsUnlimited.checked = isTtsUnlimited();
  const ttsField = form.namedItem("tts_max_tokens");
  if (ttsField && isTtsUnlimited()) {
    ttsField.dataset.previousValue = "1200";
    ttsField.value = "";
  }
  syncTtsTokenLimitUI();
  const voiceSelect = form.namedItem("voice");
  voiceSelect.innerHTML = state.voices.map((voice) => `<option value="${voice}">${voice[0].toUpperCase() + voice.slice(1)}</option>`).join("");
  voiceSelect.value = state.settings.voice;
  populateSoundSelects();
  if (elements.volumeSlider) {
    elements.volumeSlider.value = String(
      Number.isFinite(Number(state.settings.message_sound_volume))
        ? state.settings.message_sound_volume
        : 0.7
    );
    updateVolumeLabel();
  }
  populateModelSelects();
}

function populateModelSelects() {
  const form = elements.settingsForm.elements;
  const options = ['<option value="">Select a model…</option>', ...state.models.map((model) => `<option value="${escapeHTML(model.id)}">${escapeHTML(model.name)}${model.loaded ? " · loaded" : ""}</option>`)].join("");
  form.namedItem("chat_model").innerHTML = options;
  form.namedItem("tts_model").innerHTML = options;
  form.namedItem("chat_model").value = state.activeChat?.model_id || state.settings.chat_model || "";
  form.namedItem("tts_model").value = state.settings.tts_model || "";
}

function applyChatLocks() {
  if (!elements.settingsForm || !state.activeChat) return;
  const locked = Boolean(state.activeChat.messages?.length);
  const model = elements.settingsForm.elements.namedItem("chat_model");
  const prompt = elements.settingsForm.elements.namedItem("system_prompt");
  model.disabled = locked;
  prompt.disabled = locked;
  $("#model-lock-hint").textContent = locked ? "Locked for this conversation" : "Locks after your first message";
  $("#prompt-lock-hint").textContent = locked ? "Locked for this conversation" : "Locks after your first message";
  if (!locked) {
    model.value = state.activeChat.model_id || state.settings.chat_model || "";
    prompt.value = state.activeChat.system_prompt ?? state.settings.system_prompt;
  } else {
    model.value = state.activeChat.model_id || "";
    prompt.value = state.activeChat.system_prompt || "";
  }
}

let settingsTimer;
async function saveSettings() {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(async () => {
    const payload = settingsPayloadFromForm();
    try {
      state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
      if (state.activeChat && !state.activeChat.messages.length) {
        state.activeChat = await api(`/api/chats/${state.activeChat.id}`, {
          method: "PATCH",
          body: JSON.stringify({ model_id: payload.chat_model, system_prompt: payload.system_prompt }),
        });
      }
      updateHeader();
    } catch (error) { toast(error.message, "error"); }
  }, 250);
}

function settingsPayloadFromForm() {
  const form = new FormData(elements.settingsForm);
  const payload = Object.fromEntries(form.entries());
  payload.autoplay = elements.settingsForm.elements.namedItem("autoplay").checked;
  payload.message_sounds = elements.settingsForm.elements.namedItem("message_sounds")?.checked ?? true;
  payload.message_sounds_muted = elements.settingsForm.elements.namedItem("message_sounds_muted")?.checked ?? false;
  payload.wiki_enabled = elements.settingsForm.elements.namedItem("wiki_enabled").checked;
  payload.wiki_auto_on_end = elements.settingsForm.elements.namedItem("wiki_auto_on_end").checked;
  ["temperature", "top_p", "repeat_penalty"].forEach((key) => payload[key] = Number(payload[key]));
  const volume = Number(payload.message_sound_volume);
  payload.message_sound_volume = Number.isFinite(volume) ? Math.max(0, Math.min(1, volume)) : 0.7;
  const unlimited = $("#chat_tokens_unlimited")?.checked;
  if (unlimited) {
    payload.chat_max_tokens = -1;
  } else {
    const raw = Number(payload.chat_max_tokens);
    payload.chat_max_tokens = Number.isFinite(raw) && raw > 0 ? raw : 1200;
  }
  const ttsUnlimited = $("#tts_tokens_unlimited")?.checked;
  if (ttsUnlimited) {
    payload.tts_max_tokens = -1;
  } else {
    const rawTts = Number(payload.tts_max_tokens);
    payload.tts_max_tokens = Number.isFinite(rawTts) && rawTts > 0 ? rawTts : 1200;
  }
  delete payload.chat_tokens_unlimited;
  delete payload.tts_tokens_unlimited;
  return payload;
}

async function persistSettingsFromForm() {
  const payload = settingsPayloadFromForm();
  state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
  return state.settings;
}

async function browseVaultFolder() {
  if (!elements.browseVault) return;
  elements.browseVault.disabled = true;
  elements.browseVault.textContent = "Opening…";
  try {
    // Persist current path so the dialog can open near it
    try { await persistSettingsFromForm(); } catch (_) { /* still allow picker */ }
    const result = await api("/api/wiki/pick-folder", { method: "POST", body: "{}" });
    if (result.cancelled || !result.path) {
      toast("Folder selection cancelled.");
      return;
    }
    const field = elements.settingsForm.elements.namedItem("wiki_vault_path");
    field.value = result.path;
    if (!elements.settingsForm.elements.namedItem("wiki_enabled").checked) {
      elements.settingsForm.elements.namedItem("wiki_enabled").checked = true;
    }
    await persistSettingsFromForm();
    elements.wikiTestResult.className = "wiki-test-result ok";
    elements.wikiTestResult.textContent = `Selected · ${result.path}`;
    toast("Vault folder selected.");
    updateHeader();
  } catch (error) {
    toast(error.message || "Could not open folder picker", "error");
  } finally {
    elements.browseVault.disabled = false;
    elements.browseVault.textContent = "Browse…";
  }
}

async function testVault() {
  elements.wikiTestResult.className = "wiki-test-result";
  elements.wikiTestResult.textContent = "Checking…";
  try {
    await persistSettingsFromForm();
    const result = await api("/api/wiki/status");
    if (result.ok) {
      elements.wikiTestResult.className = "wiki-test-result ok";
      elements.wikiTestResult.textContent = `${result.note_count} notes · ${result.path}`;
    } else {
      elements.wikiTestResult.className = "wiki-test-result bad";
      elements.wikiTestResult.textContent = result.error || "Vault unavailable";
    }
  } catch (error) {
    elements.wikiTestResult.className = "wiki-test-result bad";
    elements.wikiTestResult.textContent = error.message;
  }
}

async function loadModels() {
  elements.serverStatus.textContent = "Connecting to LM Studio";
  elements.statusOrb.className = "status-orb";
  try {
    const result = await api("/api/models");
    state.models = result.models;
    const updates = {};
    if (!state.settings.chat_model && result.suggested_chat_model) updates.chat_model = result.suggested_chat_model;
    if (!state.settings.tts_model && result.suggested_tts_model) updates.tts_model = result.suggested_tts_model;
    if (Object.keys(updates).length) {
      state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(updates) });
      if (state.activeChat && !state.activeChat.messages.length && updates.chat_model) {
        state.activeChat = await api(`/api/chats/${state.activeChat.id}`, { method: "PATCH", body: JSON.stringify({ model_id: updates.chat_model }) });
      }
    }
    populateSettings();
    updateHeader();
    elements.serverStatus.textContent = `${state.models.filter((model) => model.loaded).length} model${state.models.filter((model) => model.loaded).length === 1 ? "" : "s"} loaded`;
    elements.serverDetail.textContent = new URL(state.settings.base_url).host;
    elements.statusOrb.className = "status-orb online";
  } catch (error) {
    elements.serverStatus.textContent = "LM Studio offline";
    elements.serverDetail.textContent = state.settings.base_url;
    elements.statusOrb.className = "status-orb error";
    toast(error.message, "error");
  }
}

function openSettings(open) {
  elements.settingsDrawer.classList.toggle("open", open);
  elements.settingsDrawer.setAttribute("aria-hidden", String(!open));
  elements.settingsBackdrop.hidden = !open;
  if (open) populateSettings();
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 170)}px`;
}

async function init() {
  try {
    const bootstrap = await api("/api/bootstrap");
    state.settings = bootstrap.settings;
    state.chats = bootstrap.chats;
    state.voices = bootstrap.voices;
    if (bootstrap.message_sounds) state.messageSoundCatalog = bootstrap.message_sounds;
    renderChatList();
    populateSettings();
    if (state.chats.length) await loadChat(state.chats[0].id); else await createChat();
    await loadModels();
  } catch (error) {
    toast(`Could not start the app: ${error.message}`, "error");
  }
}

elements.form.addEventListener("submit", sendMessage);
elements.stop.addEventListener("click", stopGeneration);
elements.input.addEventListener("input", resizeInput);
elements.input.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") elements.form.requestSubmit();
});
$("#new-chat").addEventListener("click", createChat);
$("#open-settings").addEventListener("click", () => openSettings(true));
$("#close-settings").addEventListener("click", () => openSettings(false));
elements.settingsBackdrop.addEventListener("click", () => openSettings(false));
elements.settingsForm.addEventListener("input", saveSettings);
elements.settingsForm.addEventListener("change", (event) => {
  if (event.target.id === "chat_tokens_unlimited") {
    syncChatTokenLimitUI();
  }
  if (event.target.id === "tts_tokens_unlimited") {
    syncTtsTokenLimitUI();
  }
  saveSettings();
  if (event.target.name === "base_url") setTimeout(loadModels, 450);
});
$("#refresh-models").addEventListener("click", loadModels);
$("#open-sidebar").addEventListener("click", () => elements.sidebar.classList.add("open"));
$("#close-sidebar").addEventListener("click", () => elements.sidebar.classList.remove("open"));
elements.endChat.addEventListener("click", endConversation);
elements.resumeChat.addEventListener("click", resumeConversation);
elements.lifecycleBannerAction.addEventListener("click", onLifecycleBannerAction);
elements.wikiSync.addEventListener("click", syncWiki);
elements.chatWikiToggle?.addEventListener("change", (event) => {
  setChatWikiEnabled(Boolean(event.target.checked));
});
elements.testVault.addEventListener("click", testVault);
elements.browseVault.addEventListener("click", browseVaultFolder);
elements.previewIncoming?.addEventListener("click", () => {
  const id = elements.settingsForm.elements.namedItem("message_sound_incoming")?.value;
  // Apply volume from slider immediately for preview
  if (elements.volumeSlider) {
    state.settings = { ...state.settings, message_sound_volume: Number(elements.volumeSlider.value) };
  }
  playMessageSound("incoming", { force: true, soundId: id });
});
elements.previewOutgoing?.addEventListener("click", () => {
  const id = elements.settingsForm.elements.namedItem("message_sound_outgoing")?.value;
  if (elements.volumeSlider) {
    state.settings = { ...state.settings, message_sound_volume: Number(elements.volumeSlider.value) };
  }
  playMessageSound("outgoing", { force: true, soundId: id });
});
elements.volumeSlider?.addEventListener("input", () => {
  updateVolumeLabel();
  if (state.settings) state.settings.message_sound_volume = Number(elements.volumeSlider.value);
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") { openSettings(false); elements.sidebar.classList.remove("open"); } });
// Unlock audio on first gesture (browser autoplay policy)
document.addEventListener("pointerdown", () => {
  try {
    const silent = new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=");
    silent.volume = 0.001;
    silent.play().catch(() => {});
  } catch (_) { /* ignore */ }
}, { once: true });

init();
