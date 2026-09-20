import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './assets/global.css'
import './styles/variables.css'
import './styles/tone.css'
import './styles/ai-tokens.css'
import './styles/brand-theme.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
