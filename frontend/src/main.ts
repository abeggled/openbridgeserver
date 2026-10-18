import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import i18n from './i18n'
import { scheduleTokenRefresh } from './api/client'
import './style.css'

// Gespeicherte Sitzung fortsetzen: Access-Token vor Ablauf erneuern, damit eine
// dauerhaft geöffnete Viewer-Seite nicht still den Datapoint-Scope verliert.
scheduleTokenRefresh()

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(i18n)

app.mount('#app')
