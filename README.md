# MESA-RX580

Driver Mesa com RADV ajustado para a **AMD Radeon RX 580** (Polaris 10 / Ellesmere / GFX8).

Este repositório parte do Mesa `main` e guarda só as mudanças feitas para essa placa. Não é um release oficial do Mesa. O restante da árvore continua o projeto upstream, sob a licença MIT descrita em `README.rst`.

Base gravada neste fork: Mesa 27.0.0-devel, commit `6c2135ab766` (`ac: fix off-by-one guardband computation`).

## Objetivo

A RX 580 é uma GPU de 2017 com barramento de memória largo e pouca folga de banda. O RADV atual trata o Polaris com várias travas pensadas para gerações mais novas, ou deixa caminhos úteis atrás de flag de debug. Este fork liga, só no chip da RX 580, o que o hardware já sabe fazer, e recusa emulação que gasta VRAM ou CPU.

O foco é desempenho e estabilidade no uso real da placa:

- aceitar shaders `float16` com as instruções que o Polaris realmente tem;
- cumprir `inheritedConditionalRendering` em software, em cima da predicação que o CP já faz.

## Por que “exclusivo da RX 580”

A RX 580 é Polaris 10. No kernel e no Mesa isso aparece como `CHIP_POLARIS10` e GFX8. A AMD usa esse mesmo identificador para toda a die Ellesmere: RX 470, 480, 570, 580 e 590. O driver não tem um ID separado só da marca “RX 580”. As otimizações deste fork entram quando `pdev->info.family == CHIP_POLARIS10`.

Polaris 11 (RX 460/560), Polaris 12 (RX 540/550), Fiji, Tonga e outras GCN ficam no comportamento do Mesa original. Placas GFX9+ também.

## O que mudou

### DCC

A faixa extra de DCC (alvos de cor entre 64×64 e 512×512, parte dela sem fast clear e inicializada como `DCC_UNCOMPRESSED`) foi desligada. No GFX8 o sampler lê `COMPRESSION_EN` e não aplica o código de fast clear. A metadata do color buffer e da textura saía de sincronia ao longo da sessão: a tela começava a piscar e o frametime oscilava. O DCC voltou à heurística do Mesa. Um `~/.drirc` antigo com `radv_rx580_dcc_*` não religa essa faixa.

### HTILE

HTILE compatível com o texture cache em depth array foi desligado no GFX8. O clear trata cada layer como um bloco contíguo, e um slice intercalado corrompe o vizinho. O teste de alinhamento depois do cálculo da superfície não cobria todos os casos. O piso de HTILE em depth sem MSAA não desce de 8×8.

### shaderFloat16

Arquivos: `src/amd/vulkan/radv_physical_device.c`, `src/amd/vulkan/radv_shader.c`.

A RX 580 converte FP16 (`v_cvt_f16_f32`, `v_cvt_f32_f16`) e faz load/store D16 unpacked. Não tem ALU packed de meia precisão. `v_add_f16` e `v_pk_*_f16` são GFX9+.

`shaderFloat16` fica desligado na RX 580, e um `~/.drirc` antigo não religa. A placa não tem ALU packed de FP16. Anunciar a feature faz o jogo emitir shaders de 16 bits, o driver rebaixa a conta para FP32 e o uso de VGPR sobe. Numa sessão longa entram shaders novos, o scratch cresce e o tempo de quadro oscila. Quem quiser testar usa `radv_enable_float16_gfx8`. O rebaixamento só roda em shader que realmente tem float de 16 bits.

`storageInputOutput16` não foi ligado. Varying packed de 16 bits pede a matemática que esta placa não tem.

### Fila de transferência e VRAM

A fila SDMA não fica mais ligada por padrão na RX 580. A SDMA 3.x não lê DCC nem HTILE, e cópias assíncronas ao longo da sessão corrompiam superfície. `RADV_PERFTEST=transfer_queue` ou `radv_enable_transfer_queue` ainda ligam a fila para teste.

O pin de VRAM (`RADEON_FLAG_NO_GTT_SPILL`) e o aumento de prioridade também saíram. Com o conjunto de trabalho maior do que a VRAM, o kernel passava a brigar para manter esses BOs na placa e o frametime oscilava. A colocação voltou a ser a do Mesa.

## inheritedConditionalRendering

Arquivos: `src/amd/vulkan/radv_physical_device.c`, `src/amd/vulkan/radv_cmd_buffer.c`, `src/amd/vulkan/radv_cmd_buffer.h`.

A feature `inheritedConditionalRendering` fica verdadeira só na Polaris 10. É software, em cima da predicação que o CP já emite.

Um command buffer secundário aberto com `VkCommandBufferInheritanceConditionalRenderingInfoEXT.conditionalRenderingEnable = VK_TRUE` grava draws e dispatches com o bit de predicado. O endereço do predicado do primary ainda não existe nessa hora. O secundário reserva um slot de 8 bytes. No `vkCmdExecuteCommands` o primary copia o predicado para esse slot, já normalizado: 0 descarta, qualquer outro valor desenha. A inversão do primary entra no valor, porque o secundário foi gravado com predicado “visível quando não é zero”. Oito bytes cobrem o GFX8, cuja predicação de hardware é de 64 bits com a dword alta em zero.

Draws na fila de gráficos também obedecem o `SET_PREDICATION` que o primary já armou. O slot existe para o `COND_EXEC` do compute e para o meta que restaura a predicação depois de um fast-clear ou decompress. Depois do execute, o estado de predicação do primary é mantido, como a extensão pede.

## O que este fork não liga

Nada disso existe em hardware na RX 580, e não foi emulado:

- rasterização conservadora;
- sparse residency multi-sample;
- advanced blend;
- DCC de MSAA;
- matemática packed FP16;
- decodificação de ETC2, ASTC ou outro formato que o chip não amostra nativo, quando isso significaria shader ou CPU extra e mais VRAM.

Formatos que o Mesa já emula no upstream permanecem como estão. Este fork não adiciona emulação nova.

## Limites conhecidos

- DCC, HTILE de depth array, fila SDMA e pin de VRAM seguem o Mesa. As versões próprias deste fork faziam a tela piscar e o FPS oscilar depois de uma sessão longa.
- `shaderFloat16` não dobra a taxa de ALU. O ganho está em carga, armazenagem e conversão de 16 bits.
- Dois primaries executando ao mesmo tempo o mesmo secundário com predicados diferentes compartilham o slot de predicado desse secundário. Uso simultâneo com predicados distintos pode correr.

## Painel

`rx580/mesa-rx580-panel.py` é uma janela GTK 4. Ela grava as opções `radv_rx580_*` em `~/.drirc`. O RADV lê esse arquivo quando o dispositivo Vulkan é criado. Fechar e abrir o jogo aplica a mudança. Não é preciso recompilar o driver para mexer nos limiares.

O que a janela ainda controla, só na Polaris 10:

- piso do HTILE em depth sem MSAA, no mínimo 8;
- `inheritedConditionalRendering`.

Aplicar apaga do `~/.drirc` as chaves antigas de DCC extra, pin de VRAM, fila SDMA e `shaderFloat16`. O driver também ignora essas chaves se elas ainda estiverem no arquivo.

## Instalar o driver e o painel

Na raiz do repositório:

```bash
./rx580/install.sh
```

Isso compila `libvulkan_radeon.so`, instala a biblioteca em `~/.local/lib/mesa-rx580/`, o ICD em `~/.local/share/vulkan/icd.d/mesa_rx580_icd.json` e o painel em `~/.local/bin/mesa-rx580-panel`. Também grava `VK_DRIVER_FILES` em `~/.config/environment.d/99-mesa-rx580.conf`. Apague esse arquivo para voltar ao Mesa do sistema. É preciso entrar de novo na sessão.

`--no-session` instala os arquivos sem trocar o driver da sessão. `--system` instala em `/usr/local`.

## Compilar só o RADV

Dependências usadas neste desenvolvimento: meson, ninja, gcc, libdrm, wayland, xcb, glslang, spirv-tools, libelf, zlib, zstd, expat.

```bash
meson setup build . \
  -Dvulkan-drivers=amd \
  -Dgallium-drivers= \
  -Dplatforms=x11,wayland \
  -Dvulkan-layers= \
  -Dllvm=disabled \
  -Dandroid-libbacktrace=disabled \
  -Dvideo-codecs=all \
  -Dgallium-va=disabled \
  -Dgallium-rusticl=false \
  -Dgles1=disabled \
  -Dgles2=disabled \
  -Dglx=disabled \
  -Degl=disabled \
  -Dgbm=disabled \
  -Dtools=[] \
  -Dbuildtype=debug \
  --wrap-mode=nofallback

ninja -C build src/amd/vulkan/libvulkan_radeon.so
```

Para testar sem substituir o Mesa do sistema, aponte o loader para o ICD gerado em `build/src/amd/vulkan/`. Confira com `vulkaninfo` se o dispositivo reportado é a RX 580 e se `shaderFloat16`, a fila de transfer e `inheritedConditionalRendering` aparecem nela.

## Arquivos tocados

- `src/amd/vulkan/radv_image.c`
- `src/amd/vulkan/radv_image.h`
- `src/amd/vulkan/radv_cmd_buffer.c`
- `src/amd/vulkan/radv_cmd_buffer.h`
- `src/amd/vulkan/radv_physical_device.c`
- `src/amd/vulkan/radv_shader.c`
- `src/amd/vulkan/radv_device_memory.c`
- `src/amd/vulkan/radv_radeon_winsys.h`
- `src/amd/vulkan/winsys/amdgpu/radv_amdgpu_bo.c`
- `README.md`
