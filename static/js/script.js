(function () {
    "use strict";

    // 1. Операция түріне қарай санаттарды сүзу (Шығыс / Кіріс).
    document.querySelectorAll("[data-transaction-form]").forEach(function (form) {
        var select = form.querySelector('select[name="category"]');
        if (!select) return;

        var options = Array.prototype.map.call(select.options, function (option) {
            return { value: option.value, label: option.textContent, type: option.dataset.type };
        });

        function syncCategories() {
            var checked = form.querySelector('input[name="type"]:checked');
            var type = checked ? checked.value : "expense";
            var previous = select.value;

            select.innerHTML = "";
            options
                .filter(function (item) { return item.type === type; })
                .forEach(function (item) {
                    select.appendChild(new Option(item.label, item.value));
                });

            if (Array.prototype.some.call(select.options, function (o) { return o.value === previous; })) {
                select.value = previous;
            }
        }

        form.querySelectorAll('input[name="type"]').forEach(function (radio) {
            radio.addEventListener("change", syncCategories);
        });
        syncCategories();
    });

    // 2. Жою алдында растау: <form data-confirm="...">
    document.addEventListener("submit", function (event) {
        var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });

    // 3. Құпиясөзді көрсету / жасыру.
    document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
        var target = document.getElementById(button.getAttribute("data-target"));
        if (!target) return;

        var eyeShow = button.querySelector("[data-eye-show]");
        var eyeHide = button.querySelector("[data-eye-hide]");

        button.addEventListener("click", function () {
            var reveal = target.type === "password";
            target.type = reveal ? "text" : "password";
            button.setAttribute("aria-pressed", reveal ? "true" : "false");
            button.setAttribute("aria-label", reveal ? "Құпиясөзді жасыру" : "Құпиясөзді көрсету");
            if (eyeShow) eyeShow.classList.toggle("is-hidden", reveal);
            if (eyeHide) eyeHide.classList.toggle("is-hidden", !reveal);
        });
    });

    // 4. Тіркелу: құпиясөздердің сәйкестігі және сенімділік өлшеуіші.
    var registerForm = document.querySelector("[data-register-form]");
    if (registerForm) {
        var password = registerForm.querySelector("#password");
        var confirmation = registerForm.querySelector("#password_confirm");
        var strength = registerForm.querySelector("[data-password-strength]");
        var strengthText = strength ? strength.querySelector("[data-strength-text]") : null;

        function checkMatch() {
            if (!password || !confirmation) return;
            confirmation.setCustomValidity(
                confirmation.value && confirmation.value !== password.value
                    ? "Құпиясөздер сәйкес келмейді."
                    : ""
            );
        }

        function scorePassword(value) {
            if (!value) return 0;

            var classes = 0;
            if (/[a-zа-яё]/.test(value)) classes += 1;
            if (/[A-ZА-ЯЁ]/.test(value)) classes += 1;
            if (/\d/.test(value)) classes += 1;
            if (/[^\w\s]/.test(value)) classes += 1;

            if (value.length < 8) return 1;
            if (classes <= 1 && value.length < 12) return 1;
            if (classes === 2) return 2;
            if (classes === 3) return value.length >= 12 ? 4 : 3;
            return 4;
        }

        var labels = {
            0: "Кемінде 8 таңба",
            1: "Әлсіз құпиясөз",
            2: "Орташа құпиясөз",
            3: "Жақсы құпиясөз",
            4: "Сенімді құпиясөз"
        };

        function updateStrength() {
            if (!strength || !password) return;
            var level = scorePassword(password.value);
            if (level > 0) {
                strength.setAttribute("data-level", String(level));
            } else {
                strength.removeAttribute("data-level");
            }
            if (strengthText) strengthText.textContent = labels[level];
        }

        if (password) {
            password.addEventListener("input", function () {
                updateStrength();
                checkMatch();
            });
        }
        if (confirmation) {
            confirmation.addEventListener("input", checkMatch);
        }
        updateStrength();
    }

    // 5. Жаңа картаның алдын ала көрінісі.
    var preview = document.querySelector("[data-card-preview]");
    if (preview) {
        var nameInput = document.getElementById("card-name");
        var bankInput = document.getElementById("card-bank");
        var lastFourInput = document.getElementById("card-last-four");
        var balanceInput = document.getElementById("card-balance");

        var previewName = preview.querySelector("[data-preview-name]");
        var previewBank = preview.querySelector("[data-preview-bank]");
        var previewNumber = preview.querySelector("[data-preview-number]");
        var previewBalance = preview.querySelector("[data-preview-balance]");

        function formatAmount(raw) {
            var normalised = String(raw).replace(/\s/g, "").replace(",", ".");
            var parts = normalised.split(".");
            var whole = (parts[0] || "").replace(/\D/g, "");
            var centDigits = parts.length > 1 ? parts[1].replace(/\D/g, "") : "";
            var cents = centDigits ? (centDigits + "00").slice(0, 2) : "";
            var grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
            if (!grouped && !cents) return "0";
            return cents ? grouped + "," + cents : grouped;
        }

        function setBalance(text) {
            if (!previewBalance) return;
            var first = previewBalance.firstChild;
            if (first && first.nodeType === 3) {
                first.nodeValue = text + " ";
            } else {
                previewBalance.insertBefore(document.createTextNode(text + " "), first);
            }
        }

        function updatePreview() {
            if (previewName && nameInput) {
                previewName.textContent = nameInput.value.trim() || "Карта атауы";
            }
            if (previewBank && bankInput) {
                previewBank.textContent = bankInput.value.trim() || "Банк атауы";
            }
            if (previewNumber && lastFourInput) {
                var digits = lastFourInput.value.replace(/\D/g, "").slice(0, 4);
                previewNumber.textContent = "•••• •••• •••• " + (digits || "••••");
            }
            if (balanceInput) {
                setBalance(formatAmount(balanceInput.value));
            }
        }

        [nameInput, bankInput, lastFourInput, balanceInput].forEach(function (input) {
            if (input) input.addEventListener("input", updatePreview);
        });

        // Тек сандар: картаның соңғы 4 цифры.
        if (lastFourInput) {
            lastFourInput.addEventListener("input", function () {
                lastFourInput.value = lastFourInput.value.replace(/\D/g, "").slice(0, 4);
            });
        }

        if (balanceInput) {
            balanceInput.addEventListener("blur", function () {
                balanceInput.value = formatAmount(balanceInput.value).replace(/\s/g, "");
            });
        }

        updatePreview();
    } else {
        // Карта preview жоқ беттерде де соңғы 4 цифрды сүзу.
        var lastFourOnly = document.getElementById("card-last-four");
        if (lastFourOnly) {
            lastFourOnly.addEventListener("input", function () {
                lastFourOnly.value = lastFourOnly.value.replace(/\D/g, "").slice(0, 4);
            });
        }
    }

    // 6. Профиль мәзірі: сыртқы басу және Escape арқылы жабу.
    document.querySelectorAll("[data-profile-menu]").forEach(function (menu) {
        var summary = menu.querySelector("summary");
        if (summary) {
            summary.setAttribute("aria-expanded", menu.open ? "true" : "false");
            menu.addEventListener("toggle", function () {
                summary.setAttribute("aria-expanded", menu.open ? "true" : "false");
            });
        }

        document.addEventListener("click", function (event) {
            if (menu.open && !menu.contains(event.target)) {
                menu.open = false;
            }
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;
        document.querySelectorAll("[data-profile-menu][open]").forEach(function (menu) {
            menu.open = false;
            var summary = menu.querySelector("summary");
            if (summary) summary.focus();
        });
    });

    // 7. Баптаулар бетіндегі бөлімдерді ауыстыру.
    (function setupSettingsTabs() {
        var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-settings-tab]"));
        var panels = Array.prototype.slice.call(document.querySelectorAll("[data-settings-panel]"));
        if (!tabs.length || !panels.length) return;

        function activate(name) {
            var found = false;

            panels.forEach(function (panel) {
                var active = panel.getAttribute("data-settings-panel") === name;
                panel.classList.toggle("is-active", active);
                if (active) found = true;
            });

            tabs.forEach(function (tab) {
                var active = tab.getAttribute("data-settings-tab") === name;
                tab.classList.toggle("is-active", active);
                if (active) {
                    tab.setAttribute("aria-current", "true");
                } else {
                    tab.removeAttribute("aria-current");
                }
            });

            return found;
        }

        tabs.forEach(function (tab) {
            tab.addEventListener("click", function (event) {
                var name = tab.getAttribute("data-settings-tab");
                event.preventDefault();
                activate(name);

                if (window.history && window.history.replaceState) {
                    window.history.replaceState(null, "", "#" + name);
                } else {
                    window.location.hash = name;
                }
            });
        });

        var initial = (window.location.hash || "").replace("#", "");
        if (!activate(initial || "general")) {
            activate("general");
        }

        window.addEventListener("hashchange", function () {
            var name = (window.location.hash || "").replace("#", "");
            if (name) activate(name);
        });
    })();

    // 8. Хабарламаларды басқанда жабу; сәтті хабарламалар өздігінен жоғалады.
    document.querySelectorAll(".flash").forEach(function (flash) {
        flash.addEventListener("click", function () { flash.remove(); });
        if (flash.classList.contains("flash-success")) {
            setTimeout(function () { flash.remove(); }, 4500);
        }
    });

    // 9. Құпиясөзді қалпына келтіру: қайта жіберу санауы.
    var countdown = document.querySelector("[data-resend-countdown]");
    if (countdown) {
        var seconds = parseInt(countdown.getAttribute("data-cooldown"), 10) || 0;
        var secondsNode = countdown.querySelector("[data-countdown-seconds]");
        var labelNode = countdown.querySelector("span");
        var labelHtml = labelNode ? labelNode.innerHTML : "";
        var resendForm = document.querySelector("[data-resend-form]");
        var resendButton = resendForm ? resendForm.querySelector("[data-resend-button]") : null;

        function renderCountdown() {
            if (seconds > 0) {
                if (labelNode) labelNode.innerHTML = labelHtml;
                if (secondsNode) secondsNode.textContent = String(seconds);
                if (resendButton) resendButton.disabled = true;
            } else {
                if (labelNode) labelNode.textContent = "Қайта жіберуге болады.";
                if (resendButton) resendButton.disabled = false;
            }
        }

        renderCountdown();
        if (seconds > 0) {
            var timer = setInterval(function () {
                seconds -= 1;
                if (seconds <= 0) {
                    seconds = 0;
                    clearInterval(timer);
                }
                renderCountdown();
            }, 1000);
        }
    }

    // 10. Бюджет құру: алдын ала көріністі жанды жаңарту.
    var budgetForm = document.querySelector("[data-budget-form]");
    var budgetPreview = document.querySelector("[data-budget-preview]");
    if (budgetForm && budgetPreview) {
        var nameField = budgetForm.querySelector('input[name="name"]');
        var categoryField = budgetForm.querySelector('select[name="category"]');
        var amountField = budgetForm.querySelector('input[name="amount"]');
        var startField = budgetForm.querySelector('input[name="start_date"]');
        var endField = budgetForm.querySelector('input[name="end_date"]');
        var alertField = budgetForm.querySelector('select[name="alert_percent"]');

        var outName = budgetPreview.querySelector("[data-preview-name]");
        var outAmount = budgetPreview.querySelector("[data-preview-amount]");
        var outDaily = budgetPreview.querySelector("[data-preview-daily]");
        var outThreshold = budgetPreview.querySelector("[data-preview-threshold]");
        var outDays = budgetPreview.querySelector("[data-preview-days]");

        function parseToCents(raw) {
            var normalised = String(raw || "").replace(/[\s\u00a0]/g, "").replace(",", ".");
            var parts = normalised.split(".");
            var whole = (parts[0] || "").replace(/\D/g, "");
            var digits = parts.length > 1 ? parts[1].replace(/\D/g, "") : "";
            return (parseInt(whole || "0", 10) || 0) * 100 + parseInt((digits + "00").slice(0, 2), 10);
        }

        function formatCents(cents) {
            var whole = Math.floor(cents / 100);
            var fraction = cents % 100;
            var text = String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
            return fraction ? text + "," + String(fraction).padStart(2, "0") : text;
        }

        function updateBudgetPreview() {
            var amount = parseToCents(amountField ? amountField.value : "");
            var days = 0;
            if (startField && endField && startField.value && endField.value) {
                var start = new Date(startField.value + "T00:00:00");
                var end = new Date(endField.value + "T00:00:00");
                if (!isNaN(start.getTime()) && !isNaN(end.getTime())) {
                    days = Math.max(0, Math.round((end - start) / 86400000) + 1);
                }
            }
            var percent = parseInt(alertField ? alertField.value : "80", 10) || 80;

            if (outName) {
                outName.textContent = (nameField && nameField.value.trim()) ||
                    (categoryField && categoryField.selectedIndex > 0
                        ? categoryField.options[categoryField.selectedIndex].text
                        : "Бюджет атауы");
            }
            if (outAmount) outAmount.textContent = formatCents(amount) + " ₸";
            if (outDaily) outDaily.textContent = formatCents(days ? Math.floor(amount / days) : 0) + " ₸";
            if (outThreshold) outThreshold.textContent = formatCents(Math.floor(amount * percent / 100)) + " ₸";
            if (outDays) outDays.textContent = String(days);
        }

        [nameField, categoryField, amountField, startField, endField, alertField].forEach(function (field) {
            if (field) field.addEventListener("input", updateBudgetPreview);
        });
        updateBudgetPreview();
    }

    // 11. Сұрақтар тізімі: бір уақытта бір ғана жауап ашық.
    var faqItems = Array.prototype.slice.call(document.querySelectorAll(".faq-list .faq-item"));
    faqItems.forEach(function (item) {
        item.addEventListener("toggle", function () {
            if (!item.open) return;
            faqItems.forEach(function (other) {
                if (other !== item) other.open = false;
            });
        });
    });
})();
