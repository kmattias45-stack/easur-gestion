import sqlite3
import tempfile
import csv
import io
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title='EASUR | Gestión', page_icon=':material/eco:', layout='wide')
BASE = Path(__file__).with_name('easur_gestion_v2.db')
SERVICIOS = ['Riego', 'Abono orgánico', 'Preparación de suelo', 'Asesoramiento técnico', 'Huertos urbanos', 'Gestión de residuos', 'Otro']
ESTADOS = ['Pendiente', 'En ejecución', 'Finalizado', 'Cancelado']


@contextmanager
def conexion():
    """Abrimos la base y guardamos los cambios."""
    db = sqlite3.connect(BASE)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with db:
            yield db
    finally:
        db.close()


def ejecutar(sql, datos=()):
    with conexion() as db:
        db.execute(sql, datos)
        auditar(db, sql.split()[0], sql.split()[2] if len(sql.split()) > 2 else "Registro")


def consultar(sql, datos=()):
    with conexion() as db:
        return pd.read_sql_query(sql, db, params=datos)


def iniciar_base():
    """Creamos las tablas una sola vez."""
    with conexion() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS solicitudes (id INTEGER PRIMARY KEY, codigo TEXT UNIQUE, cliente_id INTEGER REFERENCES clientes(id), servicio TEXT, superficie REAL, detalle TEXT, estado TEXT DEFAULT 'Pendiente');
        CREATE TABLE IF NOT EXISTS cotizaciones (id INTEGER PRIMARY KEY, solicitud_id INTEGER UNIQUE REFERENCES solicitudes(id), materiales REAL, mano_obra REAL, transporte REAL, margen REAL, total REAL, estado TEXT DEFAULT 'Pendiente', proyecto_id INTEGER REFERENCES proyectos(id));
        CREATE TABLE IF NOT EXISTS consumos (id INTEGER PRIMARY KEY, proyecto_id INTEGER REFERENCES proyectos(id), producto_id INTEGER REFERENCES inventario(id), cantidad REAL, fecha TEXT);
        CREATE TABLE IF NOT EXISTS historial (id INTEGER PRIMARY KEY, fecha TEXT DEFAULT CURRENT_TIMESTAMP, operador TEXT, accion TEXT, detalle TEXT);
        CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, telefono TEXT, ubicacion TEXT);
        CREATE TABLE IF NOT EXISTS proyectos (id INTEGER PRIMARY KEY, cliente_id INTEGER REFERENCES clientes(id), nombre TEXT NOT NULL, servicio TEXT, superficie REAL, responsable TEXT, fecha TEXT, estado TEXT);
        CREATE TABLE IF NOT EXISTS actividades (id INTEGER PRIMARY KEY, proyecto_id INTEGER REFERENCES proyectos(id), fecha TEXT, responsable TEXT, descripcion TEXT, horas REAL, materiales TEXT, observaciones TEXT, foto_antes BLOB, foto_despues BLOB);
        CREATE TABLE IF NOT EXISTS agenda (id INTEGER PRIMARY KEY, proyecto_id INTEGER REFERENCES proyectos(id), fecha TEXT, tarea TEXT, responsable TEXT, estado TEXT);
        CREATE TABLE IF NOT EXISTS inventario (id INTEGER PRIMARY KEY, producto TEXT UNIQUE, unidad TEXT, minimo REAL);
        CREATE TABLE IF NOT EXISTS movimientos (id INTEGER PRIMARY KEY, producto_id INTEGER REFERENCES inventario(id), fecha TEXT, tipo TEXT, cantidad REAL, detalle TEXT);
        CREATE TABLE IF NOT EXISTS finanzas (id INTEGER PRIMARY KEY, proyecto_id INTEGER REFERENCES proyectos(id), fecha TEXT, tipo TEXT, concepto TEXT, monto REAL);
        CREATE TABLE IF NOT EXISTS impacto (id INTEGER PRIMARY KEY, proyecto_id INTEGER REFERENCES proyectos(id), fecha TEXT, indicador TEXT, cantidad REAL, unidad TEXT, observacion TEXT);
        ''')


def elegir(tabla, titulo):
    """Elegimos un registro por su nombre."""
    campo = {'clientes': 'nombre', 'proyectos': 'nombre', 'inventario': 'producto'}[tabla]
    datos = consultar(f'SELECT id, {campo} FROM {tabla} ORDER BY {campo}')
    if datos.empty:
        st.info('Primero registrá los datos necesarios.')
        return None
    nombres = dict(zip(datos.id, datos[campo]))
    return st.selectbox(titulo, list(nombres), format_func=lambda i: f'{nombres[i]} (#{i})')


def tabla(datos, nombre):
    """Mostramos los datos y permitimos exportarlos."""
    if datos.empty:
        st.info('Todavía no hay registros.')
        return
    st.dataframe(datos, hide_index=True, use_container_width=True)
    st.download_button('Descargar CSV', datos.to_csv(index=False).encode('utf-8-sig'), f'{nombre}.csv', 'text/csv', key=f'csv_{nombre}')


def guardar(sql, datos, obligatorios):
    """Revisamos los campos antes de guardar."""
    if not all(str(valor).strip() for valor in obligatorios):
        st.error('Completá los campos obligatorios.')
        return
    try:
        ejecutar(sql, datos)
        st.success('Registro guardado.')
    except sqlite3.Error as error:
        st.error(f'No se pudo guardar: {error}')


def stock():
    return consultar('''SELECT i.id, i.producto, i.unidad, i.minimo,
        COALESCE(SUM(CASE WHEN m.tipo='Entrada' THEN m.cantidad ELSE -m.cantidad END),0) AS stock
        FROM inventario i LEFT JOIN movimientos m ON i.id=m.producto_id GROUP BY i.id''')


def saldo(datos):
    ingresos = datos.loc[datos.tipo == 'Ingreso', 'monto'].sum()
    gastos = datos.loc[datos.tipo == 'Gasto', 'monto'].sum()
    return ingresos, gastos


def inicio():
    st.header('Panel general')
    proyectos = consultar('SELECT * FROM proyectos')
    dinero = consultar('SELECT * FROM finanzas WHERE fecha LIKE ?', (date.today().strftime('%Y-%m')+'%',))
    ingresos, gastos = saldo(dinero)
    a,b,c,d = st.columns(4)
    a.metric('Clientes', len(consultar('SELECT id FROM clientes')))
    b.metric('Proyectos activos', int((proyectos.estado == 'En ejecución').sum()))
    c.metric('Ingresos del mes', f'Bs {ingresos:,.2f}')
    d.metric('Saldo del mes', f'Bs {ingresos-gastos:,.2f}')
    if not proyectos.empty:
        st.bar_chart(proyectos.estado.value_counts())
    st.subheader('Alertas de inventario')
    datos = stock()
    alertas = datos[datos.stock <= datos.minimo]
    if alertas.empty:
        st.info('No hay alertas de stock.')
    else:
        tabla(alertas, 'alertas')
    st.subheader('Actividades pendientes')
    vencidas = consultar("SELECT id FROM agenda WHERE estado='Pendiente' AND fecha < ?", (str(date.today()),))
    if not vencidas.empty:
        st.warning(f"{len(vencidas)} actividades vencidas")
    tabla(consultar('''SELECT a.fecha,p.nombre AS proyecto,a.tarea,a.responsable FROM agenda a JOIN proyectos p ON p.id=a.proyecto_id WHERE a.estado='Pendiente' ORDER BY a.fecha'''), 'pendientes')


def clientes():
    st.header('Clientes')
    with st.form('cliente'):
        nombre = st.text_input('Nombre completo')
        telefono = st.text_input('Teléfono o WhatsApp')
        ubicacion = st.text_input('Ubicación')
        if st.form_submit_button('Registrar cliente'):
            guardar('INSERT INTO clientes(nombre,telefono,ubicacion) VALUES (?,?,?)', (nombre.strip(),telefono,ubicacion), [nombre])
    buscar = st.text_input('Buscar cliente')
    tabla(consultar('SELECT * FROM clientes WHERE nombre LIKE ? OR ubicacion LIKE ?', (f'%{buscar}%',f'%{buscar}%')), 'clientes')
    with st.expander('Editar cliente'):
        cliente = elegir('clientes', 'Cliente para editar')
        if cliente is not None:
            actual = consultar('SELECT * FROM clientes WHERE id=?', (cliente,)).iloc[0]
            with st.form(f'editar_{cliente}'):
                nuevo = st.text_input('Nombre', actual.nombre)
                tel = st.text_input('Teléfono', actual.telefono or '')
                zona = st.text_input('Zona', actual.ubicacion or '')
                if st.form_submit_button('Guardar cambios'):
                    guardar('UPDATE clientes SET nombre=?,telefono=?,ubicacion=? WHERE id=?', (nuevo.strip(),tel,zona,cliente), [nuevo])
            st.subheader('Historial del cliente')
            st.dataframe(consultar('SELECT nombre, servicio, estado FROM proyectos WHERE cliente_id=?', (cliente,)), hide_index=True)


def proyectos():
    st.header('Proyectos')
    cliente = elegir('clientes', 'Cliente')
    if cliente is None:
        return
    with st.form('proyecto'):
        nombre = st.text_input('Nombre del proyecto')
        servicio = st.selectbox('Servicio', SERVICIOS)
        superficie = st.number_input('Superficie (m²)', min_value=0.0)
        responsable = st.text_input('Responsable')
        fecha = st.date_input('Fecha de inicio')
        if st.form_submit_button('Crear proyecto'):
            guardar('INSERT INTO proyectos(cliente_id,nombre,servicio,superficie,responsable,fecha,estado) VALUES (?,?,?,?,?,?,?)', (cliente,nombre.strip(),servicio,superficie,responsable,str(fecha),'Pendiente'), [nombre,responsable])
    proyecto = elegir('proyectos', 'Proyecto para actualizar')
    if proyecto is not None:
        estado = st.selectbox('Nuevo estado', ESTADOS)
        if st.button('Actualizar estado'):
            ejecutar('UPDATE proyectos SET estado=? WHERE id=?', (estado,proyecto))
            st.success('Estado actualizado.')
    tabla(consultar('''SELECT p.id,p.nombre,c.nombre AS cliente,p.servicio,p.superficie,p.responsable,p.fecha,p.estado FROM proyectos p JOIN clientes c ON c.id=p.cliente_id'''), 'proyectos')


def bitacora():
    """Documentamos el trabajo y las fotos de cada visita."""
    st.header('Bitácora de trabajo')
    proyecto = elegir('proyectos', 'Proyecto')
    if proyecto is None:
        return
    with st.form('actividad'):
        fecha = st.date_input('Fecha de trabajo')
        responsable = st.text_input('Responsable')
        descripcion = st.text_area('Trabajo realizado')
        horas = st.number_input('Horas trabajadas', min_value=0.0, max_value=24.0)
        materiales = st.text_area('Materiales utilizados')
        observaciones = st.text_area('Observaciones y próximos pasos')
        a,b = st.columns(2)
        antes = a.file_uploader('Foto antes', type=['png','jpg','jpeg'])
        despues = b.file_uploader('Foto después', type=['png','jpg','jpeg'])
        if st.form_submit_button('Guardar actividad'):
            if any(f and f.size > 5*1024*1024 for f in (antes,despues)):
                st.error('Máximo 5 MB por foto.')
            else:
                guardar('INSERT INTO actividades(proyecto_id,fecha,responsable,descripcion,horas,materiales,observaciones,foto_antes,foto_despues) VALUES (?,?,?,?,?,?,?,?,?)', (proyecto,str(fecha),responsable,descripcion,horas,materiales,observaciones,antes.getvalue() if antes else None,despues.getvalue() if despues else None), [responsable,descripcion])
    datos = consultar('SELECT * FROM actividades WHERE proyecto_id=? ORDER BY fecha DESC,id DESC', (proyecto,))
    for _, fila in datos.iterrows():
        with st.expander(f'{fila.fecha} | {fila.responsable} | #{fila.id}'):
            st.write(fila.descripcion)
            st.write('Horas:',fila.horas,'Materiales:',fila.materiales)
            st.write('Observaciones:',fila.observaciones)
            a,b = st.columns(2)
            for columna, campo, titulo in [(a,'foto_antes','Antes'),(b,'foto_despues','Después')]:
                if fila[campo]:
                    try:
                        columna.image(fila[campo], caption=titulo)
                    except Exception:
                        columna.warning('No se pudo mostrar la imagen.')
    tabla(datos.drop(columns=['foto_antes','foto_despues']), 'bitacora')


def agenda():
    st.header('Agenda de trabajo')
    proyecto = elegir('proyectos','Proyecto')
    if proyecto is None:
        return
    with st.form('tarea'):
        fecha = st.date_input('Fecha programada')
        tarea = st.text_input('Actividad o visita')
        responsable = st.text_input('Responsable')
        if st.form_submit_button('Programar actividad'):
            guardar('INSERT INTO agenda(proyecto_id,fecha,tarea,responsable,estado) VALUES (?,?,?,?,?)', (proyecto,str(fecha),tarea,responsable,'Pendiente'), [tarea,responsable])
    datos = consultar('SELECT * FROM agenda ORDER BY fecha')
    for _, fila in datos[datos.estado=='Pendiente'].iterrows():
        with st.container(border=True):
            st.write(f'**{fila.fecha} — {fila.tarea}**')
            st.caption(fila.responsable)
            if st.button('Marcar como realizada', key=f'tarea_{fila.id}'):
                ejecutar("UPDATE agenda SET estado='Realizada' WHERE id=?", (int(fila.id),))
                st.rerun()
    tabla(datos,'agenda')


def movimiento(producto,tipo,cantidad,detalle):
    """Controlamos el stock antes de retirar material."""
    with conexion() as db:
        db.execute('BEGIN IMMEDIATE')
        disponible = db.execute("SELECT COALESCE(SUM(CASE WHEN tipo='Entrada' THEN cantidad ELSE -cantidad END),0) FROM movimientos WHERE producto_id=?", (producto,)).fetchone()[0]
        if tipo=='Salida' and cantidad>disponible:
            return False
        db.execute('INSERT INTO movimientos(producto_id,fecha,tipo,cantidad,detalle) VALUES (?,?,?,?,?)', (producto,str(date.today()),tipo,cantidad,detalle))
        auditar(db, 'Movimiento de inventario', f'{producto}: {tipo} {cantidad}')
    return True


def inventario():
    st.header('Inventario')
    with st.form('producto'):
        nombre = st.text_input('Producto o material')
        unidad = st.selectbox('Unidad',['kg','litros','unidades','metros'])
        minimo = st.number_input('Stock mínimo',min_value=0.0)
        if st.form_submit_button('Registrar producto'):
            guardar('INSERT INTO inventario(producto,unidad,minimo) VALUES (?,?,?)',(nombre.strip(),unidad,minimo),[nombre])
    producto = elegir('inventario','Producto para movimiento')
    if producto is not None:
        with st.form('movimiento'):
            tipo = st.radio('Movimiento',['Entrada','Salida'])
            cantidad = st.number_input('Cantidad',min_value=0.01)
            detalle = st.text_input('Motivo')
            if st.form_submit_button('Guardar movimiento'):
                if movimiento(producto,tipo,cantidad,detalle):
                    st.success('Movimiento registrado.')
                else:
                    st.error('No hay suficiente stock.')
    tabla(stock(),'inventario')
    tabla(consultar('SELECT m.fecha,i.producto,m.tipo,m.cantidad,i.unidad,m.detalle FROM movimientos m JOIN inventario i ON i.id=m.producto_id ORDER BY m.id DESC'),'movimientos')


def finanzas():
    st.header('Finanzas')
    proyecto = elegir('proyectos','Proyecto')
    if proyecto is None:
        return
    with st.form('finanzas'):
        tipo = st.radio('Tipo',['Ingreso','Gasto'])
        fecha = st.date_input('Fecha')
        concepto = st.text_input('Concepto')
        monto = st.number_input('Monto (Bs)',min_value=0.01,step=1.0)
        if st.form_submit_button('Registrar movimiento'):
            guardar('INSERT INTO finanzas(proyecto_id,fecha,tipo,concepto,monto) VALUES (?,?,?,?,?)',(proyecto,str(fecha),tipo,concepto,round(monto,2)),[concepto])
    datos = consultar('SELECT * FROM finanzas WHERE proyecto_id=?',(proyecto,))
    ingresos,gastos = saldo(datos)
    a,b,c = st.columns(3)
    a.metric('Ingresos',f'Bs {ingresos:,.2f}')
    b.metric('Gastos',f'Bs {gastos:,.2f}')
    c.metric('Saldo registrado',f'Bs {ingresos-gastos:,.2f}')
    tabla(datos,'finanzas')


def impacto():
    st.header('Impacto ambiental y social')
    proyecto = elegir('proyectos','Proyecto')
    if proyecto is None:
        return
    indicadores = {'Residuos recuperados':'kg','Abono producido':'kg','Superficie atendida':'m²','Agua ahorrada medida':'litros','Personas capacitadas':'personas'}
    indicador = st.selectbox('Indicador',list(indicadores))
    unidad = indicadores[indicador]
    with st.form('impacto'):
        fecha = st.date_input('Fecha de medición')
        cantidad = st.number_input(f'Cantidad ({unidad})',min_value=0.0)
        observacion = st.text_area('¿Cómo se obtuvo el resultado?')
        if st.form_submit_button('Guardar medición'):
            guardar('INSERT INTO impacto(proyecto_id,fecha,indicador,cantidad,unidad,observacion) VALUES (?,?,?,?,?,?)',(proyecto,str(fecha),indicador,cantidad,unidad,observacion),[observacion])
    tabla(consultar('SELECT * FROM impacto WHERE proyecto_id=?',(proyecto,)),'impacto')


def informes():
    st.header('Informes por proyecto')
    proyecto = elegir('proyectos','Proyecto')
    if proyecto is None:
        return
    tabla(consultar('SELECT nombre,servicio,responsable,estado FROM proyectos WHERE id=?',(proyecto,)),'informe_proyecto')
    trabajos = consultar('SELECT fecha,responsable,descripcion,horas,materiales,observaciones FROM actividades WHERE proyecto_id=?',(proyecto,))
    st.metric('Horas trabajadas',trabajos.horas.sum())
    st.subheader('Trabajos realizados')
    tabla(trabajos,'informe_trabajos')
    st.subheader('Resumen económico')
    tabla(consultar('SELECT tipo,SUM(monto) AS total FROM finanzas WHERE proyecto_id=? GROUP BY tipo',(proyecto,)),'informe_economico')
    st.subheader('Mediciones documentadas')
    tabla(consultar('SELECT fecha,indicador,cantidad,unidad,observacion FROM impacto WHERE proyecto_id=?',(proyecto,)),'informe_impacto')



def auditar(db, accion, detalle):
    """Guardamos una nota de cada cambio."""
    db.execute('INSERT INTO historial(operador,accion,detalle) VALUES (?,?,?)',
               (st.session_state.get('operador', 'Demo'), accion, detalle))


def solicitudes():
    """Recibimos pedidos y los dejamos listos para cotizar."""
    st.header('Solicitudes de clientes')
    cliente = elegir('clientes', 'Cliente del pedido')
    if cliente is not None:
        with st.form('pedido'):
            servicio = st.selectbox('Servicio', SERVICIOS)
            superficie = st.number_input('Superficie (m²)', min_value=0.0)
            detalle = st.text_area('Necesidades del cliente')
            if st.form_submit_button('Registrar solicitud'):
                guardar('INSERT INTO solicitudes(cliente_id,servicio,superficie,detalle) VALUES (?,?,?,?)',
                        (cliente,servicio,superficie,detalle), [detalle])
    with st.expander('Importar solicitudes CSV'):
        st.caption('Columnas: codigo,nombre,telefono,ubicacion,servicio,superficie,detalle. Usá datos ficticios.')
        st.download_button('Plantilla CSV', 'codigo,nombre,telefono,ubicacion,servicio,superficie,detalle\nDEMO-01,Cliente ficticio,70000000,Tarija,Riego,250,Visita técnica\n'.encode('utf-8-sig'), 'plantilla.csv', 'text/csv')
        archivo = st.file_uploader('Archivo CSV', type=['csv'])
        if archivo and st.button('Importar'):
            try:
                filas = list(csv.DictReader(io.StringIO(archivo.getvalue().decode('utf-8-sig'))))
                nuevas = importar(filas)
                st.success(f'{nuevas} solicitudes nuevas')
            except (ValueError, KeyError, UnicodeError, sqlite3.Error) as error:
                st.error(f'Importación cancelada: {error}')
    tabla(consultar('SELECT s.*,c.nombre AS cliente FROM solicitudes s JOIN clientes c ON c.id=s.cliente_id'), 'solicitudes')


def importar(filas):
    """No repetimos pedidos que ya tienen el mismo código."""
    nuevas = 0
    with conexion() as db:
        db.execute('BEGIN IMMEDIATE')
        for fila in filas:
            codigo, nombre = fila['codigo'].strip(), fila['nombre'].strip()
            superficie = float(fila['superficie'])
            if not codigo or not nombre or not fila['servicio'].strip() or not 0 <= superficie < float('inf'):
                raise ValueError('Datos de solicitud inválidos')
            if db.execute('SELECT id FROM solicitudes WHERE codigo=?', (codigo,)).fetchone():
                continue
            cliente = db.execute('SELECT id FROM clientes WHERE nombre=? AND telefono=? AND ubicacion=?', (nombre,fila['telefono'],fila['ubicacion'])).fetchone()
            cliente_id = cliente[0] if cliente else db.execute('INSERT INTO clientes(nombre,telefono,ubicacion) VALUES (?,?,?)', (nombre,fila['telefono'],fila['ubicacion'])).lastrowid
            db.execute('INSERT INTO solicitudes(codigo,cliente_id,servicio,superficie,detalle) VALUES (?,?,?,?,?)', (codigo,cliente_id,fila['servicio'],superficie,fila['detalle']))
            nuevas += 1
        auditar(db, 'Importación', f'{nuevas} solicitudes')
    return nuevas


def aprobar_cotizacion(cotizacion, responsable):
    """Una cotización aprobada crea un proyecto una sola vez."""
    with conexion() as db:
        db.execute('BEGIN IMMEDIATE')
        fila = db.execute('SELECT c.estado,s.id,s.cliente_id,s.servicio,s.superficie FROM cotizaciones c JOIN solicitudes s ON s.id=c.solicitud_id WHERE c.id=?', (cotizacion,)).fetchone()
        if not fila or fila[0] != 'Pendiente':
            return False
        _, solicitud, cliente, servicio, superficie = fila
        proyecto = db.execute('INSERT INTO proyectos(cliente_id,nombre,servicio,superficie,responsable,fecha,estado) VALUES (?,?,?,?,?,?,?)', (cliente,f'{servicio} - solicitud {solicitud}',servicio,superficie,responsable,str(date.today()),'Pendiente')).lastrowid
        db.execute("UPDATE cotizaciones SET estado='Aprobada',proyecto_id=? WHERE id=?", (proyecto,cotizacion))
        db.execute("UPDATE solicitudes SET estado='Aprobada' WHERE id=?", (solicitud,))
        auditar(db,'Cotización aprobada', f'Proyecto {proyecto}')
    return True


def cotizaciones():
    st.header('Cotizaciones y presupuestos')
    pedidos = consultar("SELECT s.id,c.nombre,s.servicio FROM solicitudes s JOIN clientes c ON c.id=s.cliente_id WHERE s.estado='Pendiente'")
    if not pedidos.empty:
        opciones = {int(f.id): f'{f.nombre} | {f.servicio} | #{f.id}' for _, f in pedidos.iterrows()}
        pedido = st.selectbox('Solicitud pendiente', list(opciones), format_func=opciones.get)
        a,b,c = st.columns(3)
        materiales = a.number_input('Materiales (Bs)', min_value=0.0)
        mano = b.number_input('Mano de obra (Bs)', min_value=0.0)
        transporte = c.number_input('Transporte (Bs)', min_value=0.0)
        margen = st.number_input('Recargo sobre costos (%)', min_value=0.0, max_value=1000.0, value=20.0)
        total = round((materiales+mano+transporte)*(1+margen/100),2)
        st.metric('Precio propuesto', f'Bs {total:,.2f}')
        if st.button('Guardar cotización'):
            if total <= 0:
                st.error('Ingresá los costos.')
            else:
                with conexion() as db:
                    db.execute('INSERT INTO cotizaciones(solicitud_id,materiales,mano_obra,transporte,margen,total) VALUES (?,?,?,?,?,?)', (pedido,materiales,mano,transporte,margen,total))
                    db.execute("UPDATE solicitudes SET estado='Cotizada' WHERE id=?", (pedido,))
                    auditar(db,'Cotización creada',str(pedido))
                st.rerun()
    pendientes = consultar("SELECT id,total FROM cotizaciones WHERE estado='Pendiente'")
    if not pendientes.empty:
        opciones = dict(zip(pendientes.id,pendientes.total))
        cotizacion = st.selectbox('Cotización para aprobar', list(opciones), format_func=lambda i:f'#{i} — Bs {opciones[i]:,.2f}')
        responsable = st.text_input('Responsable del nuevo proyecto')
        if st.button('Aprobar y crear proyecto'):
            if responsable.strip() and aprobar_cotizacion(cotizacion,responsable.strip()):
                st.rerun()
            else:
                st.error('Ingresá un responsable o revisá el estado.')
    tabla(consultar('SELECT * FROM cotizaciones'), 'cotizaciones')


def consumir(proyecto, producto, cantidad):
    """Descontamos material y registramos el consumo juntos."""
    with conexion() as db:
        db.execute('BEGIN IMMEDIATE')
        disponible = db.execute("SELECT COALESCE(SUM(CASE WHEN tipo='Entrada' THEN cantidad ELSE -cantidad END),0) FROM movimientos WHERE producto_id=?", (producto,)).fetchone()[0]
        if not 0 < cantidad <= disponible:
            return False
        db.execute('INSERT INTO consumos(proyecto_id,producto_id,cantidad,fecha) VALUES (?,?,?,?)', (proyecto,producto,cantidad,str(date.today())))
        db.execute('INSERT INTO movimientos(producto_id,fecha,tipo,cantidad,detalle) VALUES (?,?,?,?,?)', (producto,str(date.today()),'Salida',cantidad,f'Proyecto #{proyecto}'))
        auditar(db,'Consumo de material',f'Proyecto {proyecto}; producto {producto}; cantidad {cantidad}')
    return True


def materiales_proyecto():
    st.header('Materiales por proyecto')
    proyecto = elegir('proyectos','Proyecto')
    producto = elegir('inventario','Material')
    if proyecto is not None and producto is not None:
        cantidad = st.number_input('Cantidad a utilizar (unidad del producto)', min_value=0.01)
        if st.button('Registrar consumo y descontar stock'):
            if consumir(proyecto,producto,cantidad):
                st.success('Consumo registrado y stock actualizado.')
            else:
                st.error('Stock insuficiente.')
    tabla(stock(),'stock_consumos')
    tabla(consultar('SELECT c.fecha,p.nombre,i.producto,c.cantidad,i.unidad FROM consumos c JOIN proyectos p ON p.id=c.proyecto_id JOIN inventario i ON i.id=c.producto_id'),'consumos')


def cobros():
    st.header('Pagos y saldos pendientes')
    datos = consultar("""SELECT p.id,p.nombre,c.total AS presupuesto,COALESCE(SUM(CASE WHEN f.tipo='Ingreso' THEN f.monto ELSE 0 END),0) AS cobrado FROM proyectos p JOIN cotizaciones c ON c.proyecto_id=p.id LEFT JOIN finanzas f ON f.proyecto_id=p.id GROUP BY p.id,c.total""")
    datos['saldo_pendiente'] = datos.presupuesto - datos.cobrado
    if not datos.empty:
        st.metric('Total pendiente de cobro', f'Bs {datos.saldo_pendiente.clip(lower=0).sum():,.2f}')
        st.caption('Los ingresos registrados en Finanzas se consideran cobros del proyecto en esta demo.')
        opciones = dict(zip(datos.id,datos.nombre))
        proyecto = st.selectbox('Proyecto para registrar pago',list(opciones),format_func=opciones.get)
        monto = st.number_input('Pago recibido (Bs)',min_value=0.01)
        if st.button('Registrar pago'):
            ejecutar('INSERT INTO finanzas(proyecto_id,fecha,tipo,concepto,monto) VALUES (?,?,?,?,?)',(proyecto,str(date.today()),'Ingreso','Pago de cliente',round(monto,2)))
            st.rerun()
    tabla(datos,'cobros')


def respaldo():
    """Copiamos la base completa, incluso las fotos."""
    with tempfile.TemporaryDirectory() as carpeta:
        destino = Path(carpeta)/'respaldo.db'
        with conexion() as origen:
            copia = sqlite3.connect(destino)
            try:
                origen.backup(copia)
            finally:
                copia.close()
        return destino.read_bytes()


def herramientas():
    st.header('Historial y respaldo')
    st.caption('El nombre del operador es informativo; no existe autenticación en esta demo.')
    tabla(consultar('SELECT * FROM historial ORDER BY id DESC'),'historial')
    st.download_button('Descargar copia de la base de datos',respaldo(),f'easur_respaldo_{date.today()}.db','application/octet-stream')
    st.caption('Para restaurar: cerrá Streamlit, guardá la base actual y reemplazala por la copia con el nombre easur_gestion_v2.db.')
    if st.button('Cargar proyecto ficticio de demostración'):
        cargar_demo()
        st.success('Datos de demostración disponibles.')


def cargar_demo():
    """Un pedido ficticio para probar el flujo completo."""
    importar([dict(codigo='DEMO-001',nombre='Cliente de demostración',telefono='70000000',ubicacion='Tarija',servicio='Riego',superficie='250',detalle='Evaluar riego de huerto ficticio')])
    with conexion() as db:
        cursor = db.execute("INSERT OR IGNORE INTO inventario(producto,unidad,minimo) VALUES ('Tubería DEMO','metros',20)")
        if cursor.rowcount:
            db.execute('INSERT INTO movimientos(producto_id,fecha,tipo,cantidad,detalle) VALUES (?,?,?,?,?)',(cursor.lastrowid,str(date.today()),'Entrada',100,'Inventario ficticio'))
            auditar(db,'Datos demo','Tubería DEMO')


def main():
    """Juntamos todas las partes del sistema."""
    iniciar_base()
    st.html('''<style>
    .cabecera {background:#143b23;color:white;padding:26px;border-radius:12px;margin-bottom:20px}
    .cabecera h1 {color:white!important;margin:0}
    [data-testid="stMetric"] {border:1px solid #75966c;border-radius:12px;padding:15px}
    .stButton button,.stFormSubmitButton button {background:#48872e;color:white;border-radius:8px}
    </style><div class="cabecera"><h1>EASUR Gestión 2.0</h1><p>Sistema Integral de Administración</p></div>''')
    paginas = {'Inicio':inicio,'Clientes':clientes,'Solicitudes':solicitudes,'Cotizaciones':cotizaciones,'Proyectos':proyectos,'Bitácora':bitacora,'Agenda':agenda,'Inventario':inventario,'Materiales por proyecto':materiales_proyecto,'Finanzas':finanzas,'Cobros':cobros,'Impacto ambiental':impacto,'Informes':informes,'Historial y respaldo':herramientas}
    st.sidebar.title('EASUR Gestión')
    st.sidebar.text_input('Operador (demo)', value='Equipo EASUR', key='operador')
    pagina = st.sidebar.radio('Menú principal',list(paginas))
    st.sidebar.caption('Python + Streamlit + SQLite')
    st.caption('Prototipo académico no oficial. Utilizá datos ficticios.')
    paginas[pagina]()


if __name__ == '__main__':
    main()
