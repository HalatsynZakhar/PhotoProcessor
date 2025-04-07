import streamlit as st



# Пример использования expander в Streamlit
st.title("Тест expander для PhotoProcessor")

# Основные настройки
st.header("Настройки")

# Создаем сворачиваемую секцию для путей
with st.expander("📁 Настройка путей", expanded=False):
    st.caption("Настройка путей для обработки файлов")
    input_path = st.text_input("Папка с исходными файлами:", value="C:/Downloads")
    output_path = st.text_input("Папка для результатов:", value="C:/Downloads/Output")
    backup_path = st.text_input("Папка для бэкапов:", value="C:/Downloads/Backup")
    
    if st.button("Сбросить пути"):
        st.write("Пути сброшены")

# Свернутые элементы настроек
with st.expander("⚙️ Расширенные настройки", expanded=False):
    enable_feature = st.checkbox("Включить расширенную функцию")
    if enable_feature:
        st.slider("Параметр", 0, 100, 50)
        st.selectbox("Режим", ["Быстрый", "Точный", "Стандартный"])

# Пример как это могло бы выглядеть в вашем приложении
st.subheader("Как должно выглядеть в приложении PhotoProcessor")
st.code("""
    # === Пути ===
    with st.expander("📁 Настройка путей", expanded=False):
        # --- Получаем путь к Загрузкам ОДИН РАЗ --- 
        user_downloads_folder = get_downloads_folder()
        
        # --- Input Path --- 
        current_input_path = get_setting('paths.input_folder_path')
        input_path_default_value = current_input_path if current_input_path else user_downloads_folder 
        input_path_val = st.text_input(
            "Папка с исходными файлами:", 
            value=input_path_default_value,
            key='path_input_sidebar'
        )
        # ... остальной код для путей ...
    """, language="python")

st.caption("Примечание: это тестовый файл для проверки Streamlit expander") 