import {defineConfig} from 'vitest/config';
import react from '@vitejs/plugin-react';
const proxy = (target:string,prefix:string) => ({target,changeOrigin:true,rewrite:(path:string)=>path.replace(new RegExp(`^${prefix}`),'')});
export default defineConfig({plugins:[react()],server:{proxy:{'/document':proxy(process.env.CONSOLE_DOCUMENT_URL || 'http://127.0.0.1:18080','/document'),'/extraction':proxy(process.env.CONSOLE_EXTRACTION_URL || 'http://127.0.0.1:18180','/extraction')}},test:{environment:'jsdom',globals:true}});
