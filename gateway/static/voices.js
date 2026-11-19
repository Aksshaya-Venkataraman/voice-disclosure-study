// Four interviewer voices, rendered with the browser's speech synthesis.
// For a controlled study, pre-render the prompts instead: put MP3 files at
// /audio/<voice>/<prompt_id>.mp3 and list them in /audio/manifest.json
// ({"william": ["tech_project", ...], ...}); recorded files take priority.
export const VOICE_PROFILES = {
  william: { lang: "en-GB", pitch: 0.9, rate: 1.0, prefer: ["Daniel", "Arthur", "Google UK English Male", "Microsoft Ryan", "Male"] },
  megan: { lang: "en-GB", pitch: 1.1, rate: 1.0, prefer: ["Serena", "Kate", "Martha", "Google UK English Female", "Microsoft Sonia", "Female"] },
  pixie: { lang: "en-GB", pitch: 1.7, rate: 1.15, prefer: ["Google UK English Female", "Samantha", "Female"] },
  robot: { lang: "en-US", pitch: 0.3, rate: 0.85, prefer: ["Fred", "Zarvox", "Microsoft David", "Google US English"] },
};

let manifest = null;

async function loadManifest() {
  if (manifest !== null) return manifest;
  try {
    const response = await fetch("/audio/manifest.json", { cache: "no-cache" });
    manifest = response.ok ? await response.json() : {};
  } catch {
    manifest = {};
  }
  return manifest;
}

function pickVoice(profile) {
  const voices = window.speechSynthesis?.getVoices() || [];
  for (const wanted of profile.prefer) {
    const match = voices.find((v) => v.name.includes(wanted) && v.lang.startsWith(profile.lang.slice(0, 2)));
    if (match) return match;
  }
  return voices.find((v) => v.lang === profile.lang) || voices.find((v) => v.lang.startsWith("en")) || null;
}

function playFile(url) {
  return new Promise((resolve) => {
    const audio = new Audio(url);
    audio.addEventListener("ended", resolve, { once: true });
    audio.addEventListener("error", resolve, { once: true });
    audio.play().catch(resolve);
  });
}

function synthesize(text, profile) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve();
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = profile.lang;
    utterance.pitch = profile.pitch;
    utterance.rate = profile.rate;
    const voice = pickVoice(profile);
    if (voice) utterance.voice = voice;
    utterance.addEventListener("end", resolve, { once: true });
    utterance.addEventListener("error", resolve, { once: true });
    window.speechSynthesis.speak(utterance);
  });
}

export async function speak(prompt) {
  const profile = VOICE_PROFILES[prompt.voice] || VOICE_PROFILES.megan;
  const files = await loadManifest();
  if (files[prompt.voice]?.includes(prompt.id)) {
    return playFile(`/audio/${encodeURIComponent(prompt.voice)}/${encodeURIComponent(prompt.id)}.mp3`);
  }
  return synthesize(prompt.text, profile);
}
