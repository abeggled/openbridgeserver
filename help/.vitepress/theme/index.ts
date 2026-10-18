// Extends VitePress's default theme purely to layer on custom.css — no
// component overrides needed. See custom.css for why: aligning the color
// tokens with the Admin-GUI's Tailwind palette so the embedded help drawer
// iframe doesn't look visually out of place next to the surrounding app.
import DefaultTheme from 'vitepress/theme'
import './custom.css'

export default DefaultTheme
