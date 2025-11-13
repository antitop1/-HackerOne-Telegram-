document.querySelectorAll(".copy button").forEach((button) => {
  button.addEventListener("click", () => {
    const wrapper = button.closest(".copy").querySelector(".copy__wrapper");
    const text = wrapper.innerText;

    navigator.clipboard
      .writeText(text)
      .then(() => {
        const span = button.querySelector("span");
        const originalText = span.textContent;
        span.textContent = "Скопировано";

        setTimeout(() => {
          span.textContent = originalText;
        }, 2000);
      })
      .catch((err) => {
        console.error("Ошибка копирования: ", err);
      });
  });
});
