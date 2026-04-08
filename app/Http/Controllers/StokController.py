from datetime import datetime
from app.Models.Database import db_manager
from app.Models.SnapshotManager import SnapshotManager
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from flask import request, jsonify, session, render_template, send_file



class StokController:
    """
    Controller untuk Stok Monitoring.
    Uses SnapshotManager for instant local searches.
    """

    # ──────────── Views ────────────

    @staticmethod
    def index_page():
        """HTML Page: Server selection"""
        return render_template('index.html')

    @staticmethod
    def monitoring_page():
        """HTML Page: Monitoring dashboard"""
        server_key = session.get('selected_server')
        tanggal = request.args.get('tanggal', datetime.now().strftime('%Y-%m-%d'))

        if not server_key:
            return render_template('index.html')

        return render_template('monitoring.html', tanggal=tanggal)

    @staticmethod
    def histori_page():
        """HTML Page: Cek histori pergerakan barang"""
        server_key = session.get('selected_server')
        if not server_key:
            return render_template('index.html')
        return render_template('histori.html')

    # ──────────── Server Session APIs ────────────

    @staticmethod
    def get_server_list():
        """API: Dapatkan list server yang available"""
        try:
            servers = db_manager.get_available_servers()
            return jsonify({'status': 'success', 'servers': servers})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def select_server():
        """Set server yang dipilih ke session"""
        try:
            data = request.get_json()
            server_key = data.get('server_key')

            servers = db_manager.get_available_servers()
            server_keys = [s['key'] for s in servers]

            if server_key not in server_keys:
                return jsonify({'status': 'error', 'message': f'Server key "{server_key}" tidak valid'}), 400

            session['selected_server'] = server_key
            session.modified = True

            server_name = next((s['name'] for s in servers if s['key'] == server_key), server_key)
            return jsonify({
                'status': 'success',
                'message': f'Server "{server_name}" dipilih',
                'server_key': server_key,
                'server_name': server_name
            })

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def get_current_server():
        """Get server yang sedang dipilih di session"""
        server_key = session.get('selected_server')
        if not server_key:
            return jsonify({'status': 'not_selected', 'message': 'Belum ada server yang dipilih'})

        servers = db_manager.get_available_servers()
        server = next((s for s in servers if s['key'] == server_key), None)
        return jsonify({'status': 'success', 'server': server})

    # ──────────── Snapshot APIs ────────────

    @staticmethod
    def trigger_refresh():
        """API: Trigger snapshot refresh for current server"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            tanggal = request.args.get('tanggal', datetime.now().strftime('%Y-%m-%d'))
            result = SnapshotManager.trigger_refresh(server_key, tanggal)
            return jsonify(result)

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def trigger_delta_refresh():
        """API: Quick update — only fetch new transactions since last refresh"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            tanggal = request.args.get('tanggal', datetime.now().strftime('%Y-%m-%d'))
            result = SnapshotManager.trigger_delta_refresh(server_key, tanggal)
            return jsonify(result)

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def snapshot_status():
        """API: Get snapshot status for current server"""
        server_key = session.get('selected_server')
        if not server_key:
            return jsonify({'state': 'empty', 'has_snapshot': False})

        status = SnapshotManager.get_status(server_key)
        return jsonify(status)

    @staticmethod
    def cancel_refresh():
        """API: Cancel running snapshot refresh"""
        server_key = session.get('selected_server')
        if not server_key:
            return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

        result = SnapshotManager.cancel_refresh(server_key)
        return jsonify(result)

    # ──────────── Data APIs ────────────

    @staticmethod
    def fetch_monitoring_data():
        """API: Search stok data from local snapshot (instant)"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            search_kode = request.args.get('search_kode')
            search_nama = request.args.get('search_nama')
            divisi = request.args.get('divisi')

            result = SnapshotManager.search(server_key, search_kode, search_nama, divisi)
            return jsonify(result)

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def fetch_low_stock_alert():
        """API: Get low stock items from snapshot"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            min_stok = request.args.get('min_stok', 10, type=int)
            search_kode = request.args.get('search_kode')
            search_nama = request.args.get('search_nama')

            result = SnapshotManager.search(server_key, search_kode, search_nama)

            if result['status'] != 'success':
                return jsonify(result)

            low_stock = [
                row for row in result['data']
                if 0 < (row.get('Stok Akhir', 0) or 0) < min_stok
            ]

            return jsonify({
                'status': 'success',
                'data': low_stock,
                'row_count': len(low_stock),
                'threshold': min_stok
            })

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def fetch_barang_histori():
        """API: Get transaction history for one item in one division"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            kd_barang = request.args.get('kd_barang')
            kd_divisi = request.args.get('kd_divisi', '')

            if not kd_barang:
                return jsonify({'status': 'error', 'message': 'Kode barang harus diisi'}), 400

            result = SnapshotManager.get_barang_histori(server_key, kd_barang, kd_divisi)
            return jsonify(result)

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def export_xlsx():
        """API: Export search result as XLSX file"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            search_kode = request.args.get('search_kode')
            search_nama = request.args.get('search_nama')
            divisi = request.args.get('divisi')

            result = SnapshotManager.search(server_key, search_kode, search_nama, divisi)
            if result['status'] != 'success':
                return jsonify(result), 400

            data = result['data']
            
            # Create workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Stok Monitoring"

            # Headers
            headers = [
                'Kode Divisi', 'Divisi', 'Kode Barang', 'Barang', 'Kategori', 
                'Merk', 'Model', 'Warna', 'Ukuran', 'Stok Akhir', 
                'Harga Average', 'Harga Jual', 'Nominal', 'Harga Beli Akhir'
            ]
            ws.append(headers)

            # Style headers
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center')

            # Data rows
            for row in data:
                ws.append([
                    row.get('Kode Divisi'), row.get('Divisi'), row.get('Kode Barang'), 
                    row.get('Barang'), row.get('Kategori'), row.get('Merk'), 
                    row.get('Model'), row.get('Warna'), row.get('Ukuran'), 
                    row.get('Stok Akhir'), row.get('Harga Average'), 
                    row.get('Harga Jual'), row.get('Nominal'), row.get('Harga Beli Akhir')
                ])

            # Auto-size columns
            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = (max_length + 2)
                ws.column_dimensions[column].width = adjusted_width

            # Save to buffer
            output = BytesIO()
            wb.save(output)
            output.seek(0)

            filename = f"stok_monitoring_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(
                output, 
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True, 
                download_name=filename
            )

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @staticmethod
    def export_histori_xlsx():
        """API: Export transaction history as XLSX file"""
        try:
            server_key = session.get('selected_server')
            if not server_key:
                return jsonify({'status': 'error', 'message': 'Pilih server terlebih dahulu'}), 400

            kd_barang = request.args.get('kd_barang')
            kd_divisi = request.args.get('kd_divisi', '')

            if not kd_barang:
                return jsonify({'status': 'error', 'message': 'Kode barang harus diisi'}), 400

            result = SnapshotManager.get_barang_histori(server_key, kd_barang, kd_divisi)
            if result['status'] != 'success':
                return jsonify(result), 400

            data = result['data']
            
            # Create workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Histori Barang"

            # Headers
            headers = [
                'Kd_Divisi', 'Divisi', 'K.Nota', 'Tanggal', 'Transaksi', 
                'No. Transaksi', 'Kd_Barang', 'Barang', 'Debet', 'Kredit', 
                'Kd_Satuan', 'Satuan', 'Harga', 'Saldo'
            ]
            ws.append(headers)

            # Style headers
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center')

            # Data rows with running balance
            saldo = 0
            for row in data:
                debet = float(row.get('Debet', 0) or 0)
                kredit = float(row.get('Kredit', 0) or 0)
                saldo += (debet - kredit)
                
                ws.append([
                    row.get('Kd_Divisi'), 
                    row.get('Divisi'), 
                    row.get('K.Nota'),
                    row.get('tanggal'), 
                    row.get('Transaksi'), 
                    row.get('no_transaksi'), 
                    row.get('kd_barang'), 
                    row.get('barang'), 
                    debet, 
                    kredit, 
                    row.get('kd_satuan'), 
                    row.get('satuan'), 
                    row.get('harga'),
                    saldo
                ])

            # Auto-size columns
            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = (max_length + 2)
                ws.column_dimensions[column].width = adjusted_width

            # Save to buffer
            output = BytesIO()
            wb.save(output)
            output.seek(0)

            filename = f"histori_{kd_barang}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(
                output, 
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True, 
                download_name=filename
            )

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
