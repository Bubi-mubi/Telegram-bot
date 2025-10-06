import subprocess

print("🧠 Започваме автоматично форматиране на кода...")

# 1. Премахва неизползвани импорти
subprocess.run(["autoflake", "--in-place", "--remove-unused-variables", "--remove-all-unused-imports", "--recursive", "."], check=True)

# 2. Подрежда импортите
subprocess.run(["isort", "."], check=True)

# 3. Преобразува кода по стил PEP8
subprocess.run(["black", "."], check=True)

print("✅ Форматирането приключи.")
