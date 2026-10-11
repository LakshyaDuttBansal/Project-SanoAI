"use strict";

// Change this URL only if your backend runs somewhere else.
const API_BASE_URL = "http://127.0.0.1:8000";

const authForm = document.getElementById("authForm");
const loginTab = document.getElementById("loginTab");
const signupTab = document.getElementById("signupTab");
const switchModeButton = document.getElementById("switchMode");
const formTitle = document.getElementById("formTitle");
const formSubtitle = document.getElementById("formSubtitle");
const nameField = document.getElementById("nameField");
const nameInput = document.getElementById("name");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const passwordHint = document.getElementById("passwordHint");
const loginOptions = document.getElementById("loginOptions");
const rememberCheckbox = document.getElementById("remember");
const submitButton = document.getElementById("submitButton");
const submitText = document.getElementById("submitText");
const togglePasswordButton = document.getElementById("togglePassword");
const messageBox = document.getElementById("message");
const forgotPasswordButton = document.getElementById("forgotPassword");

let currentMode = "login";

function showMessage(text, type = "error") {
  messageBox.textContent = text;
  messageBox.className = `message ${type}`;
  messageBox.hidden = false;
}

function clearMessage() {
  messageBox.textContent = "";
  messageBox.hidden = true;
  messageBox.className = "message";
}

function setMode(mode) {
  currentMode = mode;
  const isSignup = mode === "signup";

  clearMessage();

  formTitle.textContent = isSignup ? "Create your account" : "Welcome back";
  formSubtitle.textContent = isSignup
    ? "Join Sano AI and organize your health journey."
    : "Sign in to your account to continue.";

  submitText.textContent = isSignup ? "Create Account" : "Sign In";

  loginTab.classList.toggle("active", !isSignup);
  signupTab.classList.toggle("active", isSignup);

  loginTab.setAttribute("aria-selected", String(!isSignup));
  signupTab.setAttribute("aria-selected", String(isSignup));

  nameField.hidden = !isSignup;
  nameInput.required = isSignup;

  loginOptions.hidden = isSignup;
  passwordHint.hidden = !isSignup;

  passwordInput.autocomplete = isSignup
    ? "new-password"
    : "current-password";

  togglePasswordButton.textContent = "Show";
  togglePasswordButton.setAttribute("aria-label", "Show password");
  passwordInput.type = "password";
}

loginTab.addEventListener("click", () => setMode("login"));
signupTab.addEventListener("click", () => setMode("signup"));

switchModeButton.addEventListener("click", () => {
  setMode(currentMode === "login" ? "signup" : "login");
});

togglePasswordButton.addEventListener("click", () => {
  const isPassword = passwordInput.type === "password";

  passwordInput.type = isPassword ? "text" : "password";
  togglePasswordButton.textContent = isPassword ? "Hide" : "Show";
  togglePasswordButton.setAttribute(
    "aria-label",
    isPassword ? "Hide password" : "Show password"
  );
});

forgotPasswordButton.addEventListener("click", () => {
  showMessage(
    "Password reset is not configured yet. Please contact your project administrator.",
    "error"
  );
});

function getApiError(data, fallback) {
  if (typeof data?.detail === "string") {
    return data.detail;
  }

  if (Array.isArray(data?.detail)) {
    return data.detail
      .map((item) => item.msg || "Invalid input")
      .join(". ");
  }

  return fallback;
}

function saveSession(data) {
  // Remove any old session before saving the new one.
  localStorage.removeItem("sano_token");
  sessionStorage.removeItem("sano_token");

  const storage = rememberCheckbox.checked
    ? localStorage
    : sessionStorage;

  storage.setItem("sano_token", data.access_token);
  storage.setItem("sano_user_id", data.user_id);
  storage.setItem("sano_user_name", data.name);

  // Also keep the token available under the conventional key.
  // Use sano_token when making authenticated API requests.
}

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearMessage();

  const email = emailInput.value.trim().toLowerCase();
  const password = passwordInput.value;

  if (!email || !password) {
    showMessage("Please enter your email and password.");
    return;
  }

  if (currentMode === "signup") {
    const name = nameInput.value.trim();

    if (!name) {
      showMessage("Please enter your full name.");
      nameInput.focus();
      return;
    }

    if (password.length < 8) {
      showMessage("Please use a password with at least 8 characters.");
      passwordInput.focus();
      return;
    }
  }

  const endpoint = currentMode === "signup"
    ? "/api/auth/signup"
    : "/api/auth/login";

  const payload = currentMode === "signup"
    ? { name: nameInput.value.trim(), email, password }
    : { email, password };

  submitButton.disabled = true;
  submitText.textContent = currentMode === "signup"
    ? "Creating account..."
    : "Signing in...";

  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        getApiError(data, "Authentication failed. Please try again.")
      );
    }

    if (!data.access_token || !data.user_id) {
      throw new Error("The server returned an incomplete login response.");
    }

    saveSession(data);

    showMessage(
      currentMode === "signup"
        ? "Account created successfully! Opening your dashboard..."
        : "Login successful! Opening your dashboard...",
      "success"
    );

    // The backend and frontend must both be running.
    window.location.assign("overview.html");

  } catch (error) {
    console.error("Sano AI authentication error:", error);

    if (error instanceof TypeError) {
      showMessage(
        "Cannot connect to the backend. Check that FastAPI is running at " +
        API_BASE_URL + " and CORS is configured correctly."
      );
    } else {
      showMessage(error.message || "Something went wrong. Please try again.");
    }
  } finally {
    submitButton.disabled = false;
    submitText.textContent = currentMode === "signup"
      ? "Create Account"
      : "Sign In";
  }
});

// Default to the login view.
setMode("login");