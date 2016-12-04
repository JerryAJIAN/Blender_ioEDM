
import bpy

import itertools
import os
from .edm.types import Material, VertexFormat, Texture, Node, RenderNode, RootNode, EDMFile
from .edm.mathtypes import Matrix, vector_to_edm, matrix_to_edm, Vector
from .edm.basewriter import BaseWriter

def write_file(filename, options={}):

  # Start by: Assembling the materials
  # Go through every object in the scene and grab it's first material
  # Only allow meshes for now
  all_MeshObj = [x for x in bpy.context.scene.objects if x.type == "MESH"]

  # This will make sure that we only create materials that are used
  all_Materials = [obj.material_slots[0].material for obj in all_MeshObj]
  materialMap = {m.name: create_material(m) for m in all_Materials}
  materials = []
  for i, bMat in enumerate(all_Materials):
    mat = materialMap[bMat.name]
    mat.index = i
    materials.append(mat)

  # Now, build each RenderNode object
  renderNodes = []
  for obj in [x for x in all_MeshObj]:
    material = materialMap[obj.material_slots[0].material.name]
    node = create_rendernode(obj, material, options)
    renderNodes.append(node)

  # Build the nodelist from the renderNode parents
  nodes = [Node()]
  for rn in renderNodes:
    if rn.parent == None:
      rn.parent = 0
    else:
      nodes.append(rn.parent)
      rn.parent = len(nodes)-1
    rn.material = rn.material.index

  # Materials:    √
  # Render Nodes: √
  # Parents:      √
  # Let's build the root node
  root = RootNode()
  root.materials = materials
  bboxmin, bboxmax = calculate_world_bounds()
  root.boundingBoxMin = vector_to_edm(bboxmin)
  root.boundingBoxMax = vector_to_edm(bboxmax)
  
  # And finally the wrapper
  file = EDMFile()
  file.root = root
  file.nodes = nodes
  file.renderNodes = renderNodes

  writer = BaseWriter(filename)
  file.write(writer)
  writer.close()


def calculate_world_bounds():
  mins = [1e38, 1e38, 1e38]
  maxs = [-1e38, -1e38, -1e38]
  for obj in bpy.context.scene.objects:
    if obj.type in ["CAMERA"]:
      continue
    points = [obj.matrix_world * Vector(x) for x in obj.bound_box]
    for index in range(3):
      mins[index] = min([point[index] for point in points] + [mins[index]])
      maxs[index] = max([point[index] for point in points] + [maxs[index]])
  return Vector(mins), Vector(maxs)

def create_texture(source):
  # Get the texture name stripped of ALL extensions
  texName = os.path.basename(source.texture.image.filepath)
  texName = texName[:texName.find(".")]
  
  # Work out the channel for this texture
  if source.use_map_color_diffuse:
    index = 0
  elif source.use_map_normal:
    index = 1
  elif source.use_map_specular:
    index = 2

  # For now, assume identity transformation until we understand
  matrix = Matrix()
  return Texture(index=index, name=texName, matrix=matrix)

def create_material(source):
  mat = Material()
  mat.blending = int(source.edm_blending)
  mat.material_name = source.edm_material
  mat.name = source.name
  mat.uniforms = {
    "specPower": float(source.specular_hardness), # Important this is a float
    "specFactor": source.specular_intensity,
    "diffuseValue": source.diffuse_intensity,
    "reflectionValue": 0.0, # Always in uniforms, so keep here for compatibility
  }
  # No ide what this corresponds to yet:
  # "diffuseShift": Vector((0,0)),
  if source.raytrace_mirror.use:
    mat.uniforms["reflectionValue"] = source.raytrace_mirror.reflect_factor
    mat.uniforms["reflectionBlurring"] = 1.0-source.raytrace_mirror.gloss_factor
  mat.shadows.recieve = source.use_shadows
  mat.shadows.cast = source.use_cast_shadows
  mat.shadows.cast_only = source.use_cast_shadows_only

  mat.vertex_format = VertexFormat({
    "position": 4,
    "normal": 3,
    "tex0": 2
    })
  
  mat.texture_coordinates_channels = [0] + [-1]*11
  # Find the textures for each of the layers
  # Find diffuse - this will sometimes also include a translucency map
  try:
    diffuseTex = [x for x in source.texture_slots if x is not None and x.use_map_color_diffuse]
  except:
    import pdb
    pdb.set_trace()
  # normalTex = [x for x in source.texture_slots if x.use_map_normal]
  # specularTex = [x for x in source.texture_slots if x.use_map_specular]

  assert len(diffuseTex) == 1
  mat.textures.append(create_texture(diffuseTex[0]))

  return mat

def create_rendernode(source, material, options={}):
  # Always remesh, because we will want to apply transformations
  mesh = source.to_mesh(bpy.context.scene,
    apply_modifiers=options.get("apply_modifiers", False),
    settings="RENDER", calc_tessface=True)

  # Apply the local transform. IF there are no parents, then this should
  # be identical to the world transform anyway
  mesh.transform(source.matrix_local)

  # Should be more complicated for multiple layers, but will do for now
  uv_tex = mesh.tessface_uv_textures.active.data

  newVertices = []
  newIndexValues = []
  # Loop over every face, and the UV data for that face
  for face, uvFace in zip(mesh.tessfaces, uv_tex):
    # What are the new index values going to be?
    newFaceIndex = [len(newVertices)+x for x in range(len(face.vertices))]
    # Build the new vertex data
    for i, vtxIndex in enumerate(face.vertices):
      position = vector_to_edm(mesh.vertices[vtxIndex].co)
      normal = vector_to_edm(mesh.vertices[vtxIndex].normal)
      uv = [uvFace.uv[i][0], -uvFace.uv[i][1]]
      newVertices.append(tuple(itertools.chain(position, [0], normal, uv)))

    # We either have triangles or quads. Split into triangles, based on the
    # vertex index subindex in face.vertices
    if len(face.vertices) == 3:
      triangles =  ((0, 1, 2),)
    else:
      triangles = ((0, 1, 2),(2, 3, 0))

    # Write each vertex of each triangle
    for tri in triangles:
      for i in tri:
        newIndexValues.append(newFaceIndex[i])

  node = RenderNode()
  node.name = source.name
  node.material = material
  node.vertexData = newVertices
  node.indexData = newIndexValues

  # Cleanup
  bpy.data.meshes.remove(mesh)
  return node