import tseslint from 'typescript-eslint';
import hooks from 'eslint-plugin-react-hooks';
export default tseslint.config({ ignores: ['dist', 'src/api-schema.d.ts', 'test-results', 'playwright-report'] }, ...tseslint.configs.recommended, { files: ['src/**/*.{ts,tsx}'], plugins: { 'react-hooks': hooks }, rules: { ...hooks.configs.recommended.rules, 'react-hooks/set-state-in-effect': 'off' } });
