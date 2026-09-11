/** @type {import('tailwindcss').Config} */
export default {
  // WHY: Tells Tailwind to scan all HTML and JSX files for class names
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
