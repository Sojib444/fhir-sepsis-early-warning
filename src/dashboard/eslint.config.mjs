// @ts-check
import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import angular from '@angular-eslint/eslint-plugin';
import angularTemplate from '@angular-eslint/eslint-plugin-template';
import templateParser from '@angular-eslint/template-parser';

export default tseslint.config(
  {
    files: ['**/*.ts'],
    extends: [
      eslint.configs.recommended,
      ...tseslint.configs.recommended,
    ],
    plugins: { '@angular-eslint': angular },
    languageOptions: {
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
      },
    },
    rules: { ...angular.configs.recommended.rules },
  },
  {
    files: ['**/*.html'],
    plugins: { '@angular-eslint/template': angularTemplate },
    languageOptions: { parser: templateParser },
    rules: {
      ...angularTemplate.configs.recommended.rules,
      ...angularTemplate.configs.accessibility.rules,
    },
  },
  {
    ignores: ['node_modules/**', 'dist/**'],
  },
);