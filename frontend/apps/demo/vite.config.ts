import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            '@chatbot-ui/core': path.resolve(__dirname, '../../packages/chatbot-ui/src/index.ts')
        }
    }
})
