import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import pluginVue from 'eslint-plugin-vue';
import vueParser from 'vue-eslint-parser';

export default tseslint.config(
  {
    // The lint script targets `apps packages` explicitly; these ignores guard
    // against build artifacts being picked up. The rest of the openbridgeserver
    // repo has its own tooling and is never linted by this config.
    ignores: ['**/dist/**', '**/node_modules/**', '**/.tmp/**', '**/coverage/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
      },
    },
  },
  {
    // Forward-looking import boundary at the lint layer too: the core layer
    // must not reach into any skin module. (Golden rules 1 + 4.)
    files: ['apps/visu/src/core/**/*.{ts,tsx,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['*skins*', '**/skins/**'],
              message: 'core/** must not import from a skin — skins read the model, never the reverse.',
            },
          ],
        },
      ],
    },
  },
  {
    // Tests read arbitrary JSON fixtures/schema; `any` is pragmatic there. The
    // upstream Python config likewise relaxes rules for test files.
    files: ['**/*.spec.ts', '**/*.test.ts', '**/tests/**/*.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
);
