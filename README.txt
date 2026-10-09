Peaky Minds: DevOps продвинутый — курс 301874, редакция 3.
Готовое приложение и служебные CLI выданы; ученик работает с консолью, HCL/YAML, SQL, HTTP и сигналами. Разработка приложения не требуется.
Основная среда: собственный Linux amd64, Bash, Docker Engine с Compose v2. Рекомендуем 16 ГБ RAM хоста и 50 ГБ диска. Изолированный GitHub-hosted Ubuntu 24.04 используется для авторской проверки. В WSL нужен исправный Docker/cgroups; лаборатория не доказана на каждом ноутбуке.
Начните с базового курса https://stepik.org/course/301758 , если Linux, Git, curl и Compose пока незнакомы.
Клонирование: git clone https://github.com/politifan/peaky-devops-advanced.git ~/advanced-lab
cd ~/advanced-lab
Установка Docker: https://docs.docker.com/engine/install/ubuntu/ либо https://docs.docker.com/engine/install/debian/
Системные утилиты: sudo apt-get update && sudo apt-get install -y curl jq unzip openssh-client ansible python3-venv
chmod +x lab scripts/*.sh
docker version
docker compose version
./lab tools
export PATH="$PWD/.tools/bin:$PATH"
Первый модуль: ./lab foundation-start stage; ./lab foundation-start dev
Модуль 4: ./lab kube-start — только для первого создания собственного dva-course.
Перед изменениями проверьте владельца, контекст и namespace. Все имена dva-* и учебные порты должны быть свободны. Не запускайте на чужом рабочем сервере.
API доступен только на loopback. Приложение не содержит HTTP-авторизацию: не публикуйте его напрямую в интернет.
Контракт: id целое, POST=201, GET/PATCH=200, пустой title=422, отсутствующая заявка=404. Старый ID нужно сохранить из своего ответа, а не копировать из вопроса.
Все обязательные инструкции — RUNBOOK.txt по папкам и шаги Stepik. ci/DEPLOY.txt объясняет локальное продвижение проверенного артефакта; self-hosted runner не требуется.
Готовые locks получены настоящим resolver на Linux/Python 3.12 и проверены CI. Изменение зависимостей требует повторной проверки; менять код приложения не надо.
./lab http --base http://127.0.0.1:18230 --out evidence/new-attempt.json
Отчёт не перезаписывает предыдущую попытку. Для старых данных добавьте --read-id ID --expected FILE. SQL-restore и HTTP-restore проверяются отдельно.
Самопроверка не отправляет баллы в Stepik. External Grader не подключён.
State, plan, runtime, private key, kubeconfig, секреты и дампы не входят в Git. Образ и открытые подтверждения связываются с commit; публикация evidence требует очистки.
Полигон односерверный: два экземпляра не доказывают физическую HA. Vault и metrics-server — дополнительные варианты, не готовые сервисы обязательного комплекта.
