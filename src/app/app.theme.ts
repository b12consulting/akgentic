//mypreset.ts
import { definePreset } from '@primeng/themes';
import Aura from '@primeng/themes/aura';

const MyPreset = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{sky.50}',
      100: '{sky.100}',
      200: '{sky.200}',
      300: '{sky.300}',
      400: '{sky.400}',
      500: '{sky.500}',
      600: '{sky.600}',
      700: '{sky.700}',
      800: '{sky.800}',
      900: '{sky.900}',
      950: '{sky.950}',
    },
    colorScheme: {
      light: {
        primary: {
          color: '{sky.800}',
          inverseColor: '{sky.100}',
          hoverColor: '{sky.600}',
          activeColor: '{sky.500}',
        },
        highlight: {
          background: '{sky.900}',
          focusBackground: '{sky.600}',
          color: '{sky.100}',
          focusColor: '{sky.200}',
        },
      },
    },
  },
});

export default MyPreset;
