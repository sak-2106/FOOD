let authMode = "login"; // "login" | "signup"

document.addEventListener("DOMContentLoaded", () => {
  plantProduce();

  // already signed in? skip straight to the app
  if (getCurrentUser()) {
    window.location.href = "dashboard.html";
    return;
  }

  wireAuth();
  renderAuthMode();
});

function wireAuth() {
  const form = document.getElementById("authForm");
  const switchBtn = document.getElementById("switchBtn");

  switchBtn.addEventListener("click", () => {
    authMode = authMode === "login" ? "signup" : "login";
    renderAuthMode();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await submitAuth();
  });
}

function renderAuthMode() {
  const nameField = document.getElementById("nameField");
  const title = document.getElementById("authTitle");
  const subtitle = document.getElementById("authSubtitle");
  const submit = document.getElementById("authSubmit");
  const prompt = document.getElementById("switchPrompt");
  const switchBtn = document.getElementById("switchBtn");
  const errBox = document.getElementById("authError");

  errBox.hidden = true;

  if (authMode === "signup") {
    nameField.hidden = false;
    title.textContent = "Create your account";
    subtitle.textContent = "One label at a time — see past the marketing on the front of the box.";
    submit.querySelector("span").textContent = "Create account";
    prompt.textContent = "Already have an account?";
    switchBtn.textContent = "Sign in";
  } else {
    nameField.hidden = true;
    title.textContent = "Read the label first";
    subtitle.textContent = "Sign in to scan a package and see what's actually in it.";
    submit.querySelector("span").textContent = "Continue to FoodLens";
    prompt.textContent = "New here?";
    switchBtn.textContent = "Create an account";
  }
}

async function submitAuth() {
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const name = document.getElementById("name").value.trim();
  const submit = document.getElementById("authSubmit");

  document.getElementById("authError").hidden = true;

  if (!email || !password) {
    return showAuthError("Enter your email and password to continue.");
  }
  if (authMode === "signup" && !name) {
    return showAuthError("Add your name to create the account.");
  }

  submit.disabled = true;

  try {
    const endpoint = authMode === "signup" ? "/api/register" : "/api/login";
    const body = authMode === "signup" ? { name, email, password } : { email, password };

    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

    const data = await res.json();

    if (!res.ok || !data.success) {
      throw new Error(data.message || "That didn't go through. Try again.");
    }

    if (authMode === "signup") {
      authMode = "login";
      renderAuthMode();
      document.getElementById("password").value = "";
      showAuthError("Account created. Sign in to continue.", true);
    } else {
      sessionStorage.setItem("foodlens_user", JSON.stringify(data.user));
      window.location.href = "dashboard.html";
    }
  } catch (err) {
    showAuthError(err.message);
  } finally {
    submit.disabled = false;
  }
}

function showAuthError(message, isNotice) {
  const errBox = document.getElementById("authError");
  errBox.textContent = message;
  errBox.hidden = false;
  if (isNotice) {
    errBox.style.background = "var(--good-bg)";
    errBox.style.borderColor = "rgba(95,217,138,0.4)";
    errBox.style.color = "var(--good)";
  } else {
    errBox.style.background = "";
    errBox.style.borderColor = "";
    errBox.style.color = "";
  }
}
