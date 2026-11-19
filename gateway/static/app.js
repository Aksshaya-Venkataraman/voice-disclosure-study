import { speak } from "./voices.js";

const app = document.getElementById("app");
let code = null;
let session = null;
let recorder = null;
let chunks = [];
let startedAt = 0;
let timer = null;

// ------------------------------------------------------------ utilities
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child))); // text, never HTML
  }
  return node;
}

function render(...nodes) {
  app.replaceChildren(...nodes);
}

function showError(message) {
  const box = document.getElementById("error");
  if (box) {
    box.textContent = message;
    box.hidden = false;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${code}`, ...(options.headers || {}) },
    credentials: "omit",
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function readCode() {
  const match = window.location.hash.match(/code=([A-Za-z0-9_-]{20,128})/);
  if (match) {
    sessionStorage.setItem("study-code", match[1]);
    // Remove the secret from the address bar and browser history.
    history.replaceState(null, "", window.location.pathname);
    return match[1];
  }
  return sessionStorage.getItem("study-code");
}

// --------------------------------------------------------------- screens
function noLink() {
  render(el("section", { class: "card" },
    el("h1", {}, "Interview study"),
    el("p", {}, "Please open the personal link you were given by the research team.")));
}

function withdrawnScreen() {
  sessionStorage.removeItem("study-code");
  render(el("section", { class: "card" },
    el("h1", {}, "You have withdrawn"),
    el("p", {}, "All of your answers have been deleted. Thank you for your time.")));
}

function consentScreen() {
  const { title, contact_email: contact } = session.study;
  const form = el("form", { class: "card", onsubmit: submitConsent },
    el("h1", {}, title),
    el("p", {}, "You will take part in a short simulated job interview (about 15 minutes). An automated interviewer asks the questions aloud and you answer by speaking."),
    el("h2", {}, "What we record"),
    el("ul", {},
      el("li", {}, "A text transcript of each answer. Your voice recording is transcribed by Google Speech-to-Text and then discarded; audio is never stored."),
      el("li", {}, "Whether English is your first language."),
      el("li", {}, "No name, email or other contact details. You are identified only by a code such as P012."),
    ),
    el("h2", {}, "Your rights"),
    el("p", {}, "Taking part is voluntary. You can stop at any time and use “Withdraw” to delete everything you have said. ",
      "Questions about the study: ", el("a", { href: `mailto:${contact}` }, contact), "."),
    el("fieldset", {},
      el("legend", {}, "Is English your first language?"),
      el("label", { class: "inline" }, el("input", { type: "radio", name: "native", value: "yes", required: true }), "Yes"),
      el("label", { class: "inline" }, el("input", { type: "radio", name: "native", value: "no" }), "No"),
    ),
    el("label", { class: "inline" }, el("input", { type: "checkbox", name: "adult", required: true }), "I am 18 or older."),
    el("label", { class: "inline" }, el("input", { type: "checkbox", name: "agree", required: true }),
      "I have read this information and agree to take part."),
    el("p", { id: "error", class: "error", hidden: true, role: "alert" }),
    el("button", { type: "submit", class: "primary" }, "Start the interview"),
  );
  render(form);
}

async function submitConsent(event) {
  event.preventDefault();
  const data = new FormData(event.target);
  try {
    session = await api("/api/session/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agree: data.get("agree") === "on", adult: data.get("adult") === "on", native_english: data.get("native") === "yes" }),
    });
    show();
  } catch (error) {
    showError(error.message);
  }
}

function doneScreen() {
  render(el("section", { class: "card" },
    el("h1", {}, "Thank you!"),
    el("p", {}, "The interview is complete and your answers have been saved."),
    withdrawControl()));
}

function withdrawControl() {
  const button = el("button", { type: "button", class: "link danger", onclick: confirmWithdraw }, "Withdraw from the study");
  return el("div", { class: "withdraw", id: "withdraw" }, button);
}

function confirmWithdraw() {
  const box = document.getElementById("withdraw");
  box.replaceChildren(
    el("p", {}, "This deletes all of your answers and cannot be undone."),
    el("button", { type: "button", class: "danger", onclick: doWithdraw }, "Yes, delete my answers"),
    el("button", { type: "button", onclick: () => box.replaceChildren(withdrawControl()) }, "Cancel"),
  );
}

async function doWithdraw() {
  try {
    await api("/api/session/withdraw", { method: "POST" });
  } catch (error) {
    if (error.status !== 410) return showError(error.message);
  }
  withdrawnScreen();
}

function interviewScreen() {
  const prompt = session.prompt;
  const isQuestion = prompt.kind === "question";
  const progress = isQuestion ? `Question ${prompt.position} of ${prompt.total}` : "Getting started";
  const canRecord = session.speech_input && window.MediaRecorder && preferredMimeType();

  render(el("section", { class: "card" },
    el("p", { class: "progress" }, progress),
    el("progress", { max: prompt.total, value: isQuestion ? prompt.position - 1 : 0 }),
    el("div", { class: "interviewer" },
      el("div", { class: "avatar", "aria-hidden": "true" }, prompt.label.slice(-1)),
      el("div", {}, el("strong", {}, prompt.label), el("p", { class: "muted" }, "is asking you a question")),
    ),
    prompt.display_text ? el("p", { class: "question" }, prompt.display_text) : null,
    el("button", { type: "button", onclick: () => speak(prompt) }, "▶ Play the question again"),
    el("p", { id: "error", class: "error", hidden: true, role: "alert" }),
    canRecord
      ? el("div", { class: "recorder" },
          el("button", { type: "button", id: "record", class: "primary big", onclick: toggleRecording }, "● Start answering"),
          el("span", { id: "timer", class: "muted" }, ""))
      : el("p", { class: "muted" }, session.speech_input
          ? "Your browser cannot record audio here. Please type your answer instead (Chrome, Edge or Firefox support recording)."
          : "Please type your answer."),
    el("details", { open: !canRecord },
      el("summary", {}, "Type your answer instead"),
      el("form", { onsubmit: submitText },
        el("label", { for: "typed" }, "Your answer"),
        el("textarea", { id: "typed", rows: 5, maxlength: 5000, required: true }),
        el("button", { type: "submit" }, "Send answer"))),
    withdrawControl(),
  ));
  speak(prompt);
}

function show() {
  if (session.status === "invited") return consentScreen();
  if (session.status === "completed") return doneScreen();
  return interviewScreen();
}

// ------------------------------------------------------------- recording
function preferredMimeType() {
  const options = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/ogg"];
  return options.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || null;
}

async function toggleRecording() {
  const button = document.getElementById("record");
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  window.speechSynthesis?.cancel();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return showError("Microphone access was blocked. Allow the microphone, or type your answer below.");
  }
  const mimeType = preferredMimeType();
  chunks = [];
  recorder = new MediaRecorder(stream, { mimeType });
  recorder.addEventListener("dataavailable", (e) => e.data.size && chunks.push(e.data));
  recorder.addEventListener("stop", () => {
    stream.getTracks().forEach((t) => t.stop());
    clearInterval(timer);
    uploadRecording(new Blob(chunks, { type: mimeType.split(";")[0] }), (Date.now() - startedAt) / 1000);
  });
  recorder.start();
  startedAt = Date.now();
  button.textContent = "■ Stop and send";
  timer = setInterval(() => {
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    document.getElementById("timer").textContent = `${seconds}s`;
    if (seconds >= session.max_audio_seconds) recorder.stop();
  }, 500);
}

async function uploadRecording(blob, seconds) {
  const button = document.getElementById("record");
  button.disabled = true;
  button.textContent = "Sending…";
  const form = new FormData();
  form.append("prompt_id", session.prompt.id);
  form.append("duration_seconds", seconds.toFixed(2));
  form.append("audio", blob, blob.type.includes("ogg") ? "answer.ogg" : "answer.webm");
  await sendAnswer(form, () => {
    button.disabled = false;
    button.textContent = "● Start answering";
  });
}

async function submitText(event) {
  event.preventDefault();
  const text = document.getElementById("typed").value.trim();
  if (!text) return;
  const form = new FormData();
  form.append("prompt_id", session.prompt.id);
  form.append("text", text);
  await sendAnswer(form, () => {});
}

async function sendAnswer(form, onError) {
  try {
    session = await api("/api/session/answers", { method: "POST", body: form });
    show();
  } catch (error) {
    if (error.status === 410) return withdrawnScreen();
    if (error.status === 409) {
      session = await api("/api/session");
      return show();
    }
    onError();
    showError(error.message);
  }
}

// ------------------------------------------------------------------ start
async function main() {
  code = readCode();
  if (!code) return noLink();
  try {
    session = await api("/api/session");
    show();
  } catch (error) {
    if (error.status === 410) return withdrawnScreen();
    if (error.status === 401) return noLink();
    render(el("section", { class: "card" }, el("p", { class: "error" }, error.message)));
  }
}

// Voices load asynchronously in some browsers.
window.speechSynthesis?.addEventListener?.("voiceschanged", () => {});
main();
