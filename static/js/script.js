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

    // 3. Картаның соңғы 4 цифры: тек сандар.
    var lastFour = document.getElementById("card-last-four");
    if (lastFour) {
        lastFour.addEventListener("input", function () {
            lastFour.value = lastFour.value.replace(/\D/g, "").slice(0, 4);
        });
    }

    // 4. Тіркелу: құпиясөздердің сәйкестігі.
    var registerForm = document.querySelector("[data-register-form]");
    if (registerForm) {
        var password = registerForm.querySelector("#password");
        var confirmation = registerForm.querySelector("#password_confirm");

        function checkMatch() {
            confirmation.setCustomValidity(
                confirmation.value && confirmation.value !== password.value
                    ? "Құпиясөздер сәйкес келмейді."
                    : ""
            );
        }

        password.addEventListener("input", checkMatch);
        confirmation.addEventListener("input", checkMatch);
    }

    // 5. Хабарламаларды басқанда жабу; сәтті хабарламалар өздігінен жоғалады.
    document.querySelectorAll(".flash").forEach(function (flash) {
        flash.addEventListener("click", function () { flash.remove(); });
        if (flash.classList.contains("flash-success")) {
            setTimeout(function () { flash.remove(); }, 4500);
        }
    });
})();
