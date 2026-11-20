# MESA-RX580

Driver Mesa com RADV ajustado para a **AMD Radeon RX 580** (Polaris 10 / Ellesmere / GFX8).

Este repositório parte do Mesa `main` e guarda só as mudanças feitas para essa placa. Não é um release oficial do Mesa. O restante da árvore continua o projeto upstream, sob a licença MIT descrita em `README.rst`.

Base gravada neste fork: Mesa 27.0.0-devel, commit `6c2135ab766` (`ac: fix off-by-one guardband computation`).

## Objetivo

A RX 580 é uma GPU de 2017 com barramento de memória largo e pouca folga de banda. O RADV atual trata o Polaris com várias travas pensadas para gerações mais novas, ou deixa caminhos úteis atrás de flag de debug. Este fork liga, só no chip da RX 580, o que o hardware já sabe fazer, e recusa emulação que gasta VRAM ou CPU.

O foco é desempenho e estabilidade no uso real da placa:

- comprimir cor e depth quando isso reduz tráfego de memória sem um passe extra mais caro que o ganho;
- aceitar shaders `float16` com as instruções que o Polaris realmente tem;
- sobrepor cópias na SDMA sem corromper superfície comprimida;
- cumprir `inheritedConditionalRendering` em software, em cima da predicação que o CP já faz.

## Por que “exclusivo da RX 580”

A RX 580 é Polaris 10. No kernel e no Mesa isso aparece como `CHIP_POLARIS10` e GFX8. A AMD usa esse mesmo identificador para toda a die Ellesmere: RX 470, 480, 570, 580 e 590. O driver não tem um ID separado só da marca “RX 580”. As otimizações deste fork entram quando `pdev->info.family == CHIP_POLARIS10`.

Polaris 11 (RX 460/560), Polaris 12 (RX 540/550), Fiji, Tonga e outras GCN ficam no comportamento do Mesa original. Placas GFX9+ também.

## O que mudou

### DCC em alvos de cor médios

Arquivos: `src/amd/vulkan/radv_image.c`, `src/amd/vulkan/radv_image.h`, `src/amd/vulkan/radv_cmd_buffer.c`.

No Mesa, DCC e fast clear nascem juntos. Abaixo de 512×512 o driver recusa os dois, porque o passe de fast-clear eliminate lê e reescreve a imagem inteira. Na RX 580 esse passe perde para o ganho em alvos pequenos, mas a compressão delta do color buffer ainda vale a pena no meio do caminho.

Para a RX 580, imagens de cor de uma amostra a partir de 64×64 e até 512×512 ganham DCC mesmo quando a heurística genérica recusa o fast clear. A escolha entre fast clear e compressão normal depende do tamanho:

- abaixo de 64×64, sem DCC: a inicialização custa mais do que a banda economizada;
- de 64×64 até abaixo de 256×256, DCC sem fast clear, superfície expandida (`DCC_UNCOMPRESSED`), porque o eliminate lê e reescreve a imagem inteira;
- de 256×256 até 512×512, DCC com fast clear: o clear em si já é uma escrita grande;
- acima de 512×512, o caminho genérico do Mesa, que já faz DCC e fast clear.

O sampler do GFX8 lê DCC pelo bit `COMPRESSION_EN` e não aplica sozinho a cor de fast clear. Por isso a faixa sem fast clear não nasce com `CLEAR_0000`. MSAA com DCC continua desligado. DCC de storage image também, porque store no GFX8 descomprime.

### HTILE em depth arrays

Arquivo: `src/amd/vulkan/radv_image.c`.

Shadow maps em array são o caso comum. HTILE compatível com o texture cache deixa o depth comprimido na hora de amostrar. No GFX8 o addrlib pode intercalar os slices de HTILE. O clear do RADV trata cada layer como um bloco contíguo, e um slice intercalado corrompe o layer vizinho.

Na RX 580 o driver pede HTILE TC-compat também para arrays. Depois que a superfície é calculada, ele mede o alinhamento do slice (`meta_slice_size` contra `meta_alignment_log2`). Se os slices estão intercalados, a superfície é recalculada sem a flag. Arrays cujo slice já é alinhado ao pipe permanecem comprimidos na leitura.

HTILE com mipmaps no GFX8 continua desligado. O clear parcial de mip nesse nível não está implementado.

### shaderFloat16

Arquivos: `src/amd/vulkan/radv_physical_device.c`, `src/amd/vulkan/radv_shader.c`.

A RX 580 converte FP16 (`v_cvt_f16_f32`, `v_cvt_f32_f16`) e faz load/store D16 unpacked. Não tem ALU packed de meia precisão. `v_add_f16` e `v_pk_*_f16` são GFX9+.

O fork anuncia `shaderFloat16` na Polaris 10 sem depender de `radv_enable_float16_gfx8`. O compilador, em GFX8, rebaixa a ALU float16 para FP32 e conserva as conversões e o tráfego de 16 bits na memória. O resultado numérico segue o FP32 arredondado de volta para FP16. A flag antiga do drirc continua como opt-in manual para outro GFX8.

`storageInputOutput16` não foi ligado. Varying packed de 16 bits pede a matemática que esta placa não tem.

### Fila de transferência

Arquivo: `src/amd/vulkan/radv_physical_device.c`.

A RX 580 tem SDMA 3.x. O Mesa só expõe a fila de transfer com `RADV_PERFTEST=transfer_queue` ou com a opção de drirc `radv_enable_transfer_queue`. Neste fork a fila fica visível por padrão na Polaris 10.

A SDMA dessa geração não lê DCC nem HTILE. `radv_sdma_supports_image` devolve essas cópias para o compute, então a fila assíncrona não desliga compressão nem copia bloco comprimido como se fosse texel cru. Cópias de buffer e de imagem já descomprimida podem ir para a SDMA. `RADV_DEBUG=notransfer` ainda desliga a fila.

A coerência entre CP e SDMA no GFX6–8 já está no Mesa: nenhum dos dois usa a L2, e o postamble da fila de gráficos esvazia CB/DB antes do fence. `radv_queue.c` não precisou de lógica nova.

### VRAM na RX 580

Arquivos: `src/amd/vulkan/radv_device_memory.c`, `src/amd/vulkan/radv_radeon_winsys.h`, `src/amd/vulkan/winsys/amdgpu/radv_amdgpu_bo.c`.

O RADV, por padrão, marca alocações de VRAM também como GTT. O kernel prefere VRAM no início e pode derramar o BO para a RAM do sistema quando a placa enche. `RADV_PERFTEST=nogttspill` desliga isso para tudo e, com a VRAM lotada, a alocação falha.

Neste fork o derramamento é recusado só na Polaris 10, e só para memória device-local sem acesso da CPU:

- imagem dedicada de color attachment ou depth/stencil;
- buffer dedicado de pelo menos 256 KiB.

Esses BOs recebem `RADEON_FLAG_NO_GTT_SPILL`. A prioridade sobe para 26 em attachment e 22 em buffer grande, sem ultrapassar uma prioridade que a aplicação já tenha pedido. Heaps device-local de 8 MiB ou mais, típicos do suballocator de render target, continuam podendo ir para GTT, mas com prioridade 18, acima do padrão 0,5. Memória visível pela CPU, exportada ou já colocada em GTT não entra nessa regra.

Se a VRAM não couber o BO preso, a alocação é tentada de novo com spill. A aplicação não toma `VK_ERROR_OUT_OF_DEVICE_MEMORY` só porque o pin falhou.

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

- Fast clear em alvos de 128² a 512² fica de fora de propósito. O clear vira desenho pelo color buffer, que ainda comprime via DCC.
- HTILE de array só permanece TC-compat quando o slice não está intercalado. No caso intercalado o comportamento volta ao Mesa: HTILE sem leitura comprimida. Em depth sem MSAA o piso de tamanho cai de 8×8 para 4×4. Mipmaps continuam sem HTILE no GFX8.
- `shaderFloat16` não dobra a taxa de ALU. O ganho está em carga, armazenagem e conversão de 16 bits.
- Dois primaries executando ao mesmo tempo o mesmo secundário com predicados diferentes compartilham o slot de predicado desse secundário. Uso simultâneo com predicados distintos pode correr.
- A fila de transfer não acelera imagem que ainda está com DCC ou HTILE comprimido. Essa cópia cai no compute.

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
