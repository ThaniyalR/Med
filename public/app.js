const installButton = document.querySelector("#install-app");
const installStatus = document.querySelector("#install-app-status");
const languageSelect = document.querySelector("#language-select");
const translations = window.NUTRIX_TRANSLATIONS;
const languageLocales = {
  en: "en",
  hi: "hi-IN",
  ta: "ta-IN",
  te: "te-IN",
  kn: "kn-IN",
  ml: "ml-IN",
  bn: "bn-IN",
  mr: "mr-IN",
  gu: "gu-IN",
  pa: "pa-IN",
};
let installPrompt;
let currentLanguage = "en";
const originalTexts = new WeakMap();
const originalAttributes = new WeakMap();

function readSavedLanguage() {
  try {
    return localStorage.getItem("nutrix-language");
  } catch (error) {
    console.error("Unable to read the saved Nutrix language preference.", error);
    return null;
  }
}

function saveLanguage(language) {
  try {
    localStorage.setItem("nutrix-language", language);
  } catch (error) {
    console.error("Unable to save the Nutrix language preference.", error);
  }
}

function translateCount(text, dictionary) {
  const match = text.match(/^(\d+)\s+(available|open|total|years experience)$/);
  if (match) {
    const translatedLabel = dictionary[match[2]];
    if (translatedLabel) {
      return `${match[1]} ${translatedLabel}`;
    }
  }

  const verificationMatch = text.match(/^No (Pending|Approved|Rejected) accounts to show\.$/);
  if (verificationMatch && dictionary["No {{ status }} accounts to show."]) {
    return dictionary["No {{ status }} accounts to show."].replace(
      "{{ status }}",
      dictionary[verificationMatch[1]] || verificationMatch[1]
    );
  }

  const verificationStatusMatch = text.match(
    /^(Account verification|Credential verification): (Pending|Approved|Rejected)$/
  );
  if (verificationStatusMatch) {
    const label = dictionary[`${verificationStatusMatch[1]}:`];
    const status = dictionary[verificationStatusMatch[2]];
    if (label && status) {
      return `${label} ${status}`;
    }
  }

  const verificationFlashMatch = text.match(
    /^Account verification (Pending|Approved|Rejected)\.$/
  );
  if (verificationFlashMatch && dictionary["Account verification "]) {
    return `${dictionary["Account verification "]}${dictionary[verificationFlashMatch[1]]}.`;
  }

  const badgeMatch = text.match(/^(Pending|Approved|Rejected) verification$/);
  if (badgeMatch && dictionary.verification) {
    return `${dictionary[badgeMatch[1]]} ${dictionary.verification}`;
  }

  const welcomeMatch = text.match(/^Welcome, (.+)$/);
  if (welcomeMatch && dictionary["Welcome,"]) {
    return `${dictionary["Welcome,"]} ${welcomeMatch[1]}`;
  }

  const requestMatch = text.match(/^Care request #(\d+)$/);
  if (requestMatch && dictionary["Care request #"]) {
    return `${dictionary["Care request #"]}${requestMatch[1]}`;
  }

  return dictionary[text];
}

function applyLanguage(language) {
  const dictionary = translations[language] || translations.en;
  const locale = languageLocales[language] || languageLocales.en;
  currentLanguage = languageLocales[language] ? language : "en";
  document.documentElement.lang = locale;

  const walker = document.createTreeWalker(
    document.body,
    NodeFilter.SHOW_TEXT,
    {
      acceptNode(node) {
        return node.parentElement?.closest("script, style, [data-user-content]")
          ? NodeFilter.FILTER_REJECT
          : NodeFilter.FILTER_ACCEPT;
      },
    }
  );
  let node;
  while ((node = walker.nextNode())) {
    if (!originalTexts.has(node)) {
      originalTexts.set(node, node.nodeValue);
    }
    const original = originalTexts.get(node);
    const trimmed = original.trim();
    const translated = dictionary[trimmed] || translateCount(trimmed, dictionary);
    if (translated) {
      node.nodeValue = original.replace(trimmed, translated);
    } else {
      node.nodeValue = original;
    }
  }

  document.querySelectorAll("[placeholder], [aria-label], [title]").forEach((element) => {
    if (!originalAttributes.has(element)) {
      const attributes = {};
      for (const attribute of ["placeholder", "aria-label", "title"]) {
        if (element.hasAttribute(attribute)) {
          attributes[attribute] = element.getAttribute(attribute);
        }
      }
      originalAttributes.set(element, attributes);
    }
    for (const [attribute, original] of Object.entries(originalAttributes.get(element))) {
      element.setAttribute(attribute, dictionary[original] || original);
    }
  });

  if (!document.originalEnglishTitle) {
    document.originalEnglishTitle = document.title;
  }
  const originalTitle = document.originalEnglishTitle;
  const titleParts = originalTitle.match(/^(.*?) \| Nutrix$/);
  document.title = titleParts
    ? `${dictionary[titleParts[1]] || titleParts[1]} | Nutrix`
    : dictionary[originalTitle] || originalTitle;
  languageSelect.value = languageLocales[language] ? language : "en";
}

const savedLanguage = readSavedLanguage();
applyLanguage(languageLocales[savedLanguage] ? savedLanguage : "en");

languageSelect.addEventListener("change", () => {
  saveLanguage(languageSelect.value);
  applyLanguage(languageSelect.value);
});

document.querySelectorAll("form[data-single-submit]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }

    form.dataset.submitting = "true";
    const submitButtons = form.querySelectorAll(
      'button[type="submit"], button:not([type]), input[type="submit"]'
    );
    submitButtons.forEach((button) => {
      button.disabled = true;
    });
  });
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch((error) => {
    console.error("Nutrix service worker registration failed.", error);
  });
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  installButton.hidden = false;
});

installButton.addEventListener("click", async () => {
  if (!installPrompt) {
    return;
  }

  installPrompt.prompt();
  const { outcome } = await installPrompt.userChoice;
  installStatus.textContent =
    outcome === "accepted"
      ? translations[currentLanguage]["Nutrix is installing."]
      : "";
  installPrompt = null;
  installButton.hidden = true;
});

window.addEventListener("appinstalled", () => {
  installButton.hidden = true;
  installStatus.textContent = translations[currentLanguage]["Nutrix is installed on this device."];
  installPrompt = null;
});
